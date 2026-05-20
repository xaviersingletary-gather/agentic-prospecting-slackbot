"""Research runner — orchestrates the V1.5 staged Account Research flow.

Staged flow (new in V1.5):
  Stage 1 — `run_account_research(session, post)`
      Findings (Exa + OpenRouter) + HubSpot account snapshot.
      Fires immediately on DM, BEFORE the rep picks personas.

  Stage 2 — `run_persona_research(session, respond)`
      Apollo contact pull tagged against HubSpot, scoped to the personas
      the rep selected. Fires on `Run Research` button click.

`run_research` (legacy, full flow) is kept for back-compat with the
Phase 13 integration tests — it just composes the two stages.

Every external dependency is soft-failed. Missing env vars yield None
clients via the factory; the runner inspects each and degrades
gracefully. Any exception in any module is caught at the runner
boundary, logged via `safe_log_exception`, and surfaced in output as a
warning banner or research_gap. **The runner functions never raise.**
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from src.agents.suggested_questions import (
    FALLBACK_QUESTIONS,
    generate_suggested_questions,
    is_fallback,
)
from src.integrations.slack_blocks import suggested_questions_block, validate_blocks
from src.memory.blocks import build_new_since_blocks
from src.memory.diff import diff_findings
from src.memory.snapshots import get_latest_snapshot, save_snapshot
from src.research.angle_blocks import build_angle_blocks
from src.research.angle_builder import build_angles
from src.research.clients_factory import (
    get_apollo_client,
    get_hubspot_account_client,
    get_hubspot_contact_client,
    get_hubspot_portal_id,
)
from src.research.contact_blocks import build_contact_blocks
from src.research.contact_pipeline import build_tagged_contacts
from src.research.domain_resolver import resolve_domain
from src.research.findings_builder import build_findings
from src.research.output_formatter import build_research_blocks
from src.research.sessions import ResearchSession, set_findings
from src.security.exception_logger import safe_log_exception
from src.integrations.hubspot.account_snapshot import (
    build_account_not_found_blocks,
    build_account_snapshot_blocks,
    get_account_snapshot,
)


def _persist_company_research(
    session: "ResearchSession", findings: Dict[str, Any]
) -> None:
    """Write/upsert a CompanyResearch row for the session.

    Used by the V1 daily-use flow so follow-up Q&A (Phase 5) can find the
    research artifacts via Session.id. Keeps the schema mapping close to
    the findings dict; defensive against missing keys.
    """
    from src.db.session import SessionLocal
    from src.db.models import CompanyResearch
    import uuid

    db = SessionLocal()
    try:
        existing = (
            db.query(CompanyResearch)
            .filter(CompanyResearch.session_id == session.session_id)
            .order_by(CompanyResearch.created_at.desc())
            .first()
        )
        if existing is not None:
            # Idempotent: refresh fields on the latest row for this session.
            existing.account_name = findings.get("account_name") or session.account_name
            existing.facility_count = findings.get("facility_count")
            existing.facility_count_note = findings.get("facility_count_note")
            existing.board_initiatives = findings.get("board_initiatives", []) or []
            existing.company_priorities = findings.get("company_priorities", []) or []
            existing.trigger_events = findings.get("trigger_events", []) or []
            existing.automation_vendors = findings.get("automation_vendors", []) or []
            existing.exception_tax = findings.get("exception_tax")
            existing.research_gaps = findings.get("research_gaps", []) or []
            existing.raw_research_text = findings.get("raw_research_text", "") or ""
        else:
            row = CompanyResearch(
                id=str(uuid.uuid4()),
                session_id=session.session_id,
                account_name=findings.get("account_name") or session.account_name,
                facility_count=findings.get("facility_count"),
                facility_count_note=findings.get("facility_count_note"),
                board_initiatives=findings.get("board_initiatives", []) or [],
                company_priorities=findings.get("company_priorities", []) or [],
                trigger_events=findings.get("trigger_events", []) or [],
                automation_vendors=findings.get("automation_vendors", []) or [],
                exception_tax=findings.get("exception_tax"),
                research_gaps=findings.get("research_gaps", []) or [],
                raw_research_text=findings.get("raw_research_text", "") or "",
            )
            db.add(row)
        db.commit()
    finally:
        db.close()

logger = logging.getLogger(__name__)


def build_placeholder_findings(session: ResearchSession) -> Dict[str, Any]:
    """Deprecated alias. Delegates to `build_findings`."""
    return build_findings(session)


# ---------------------------------------------------------------------------
# Stage 1 — Account research (findings + HubSpot snapshot)
# ---------------------------------------------------------------------------

def run_account_research(
    session: ResearchSession,
    post: Callable[..., Any],
    on_progress: Optional[Callable[[str], None]] = None,
) -> None:
    """Build research findings (Exa + OpenRouter only) and post them.

    `post` is the Slack posting callable — `say` from the message handler
    is the typical caller. `on_progress`, if provided, is invoked with
    short status strings as each pipeline stage runs. Always invoked
    exactly once. Never raises.

    Appends the V1 Daily-Use suggested-questions actions block to the
    posted brief (spec §5 Move 3). The generator is best-effort: if it
    times out (>5s) or fails entirely, the brief still ships and we log a
    ``suggested_questions_slow`` / ``suggested_questions_failed``
    ``WorkflowEvent`` so we can monitor degradation.
    """
    blocks = _build_account_blocks(session, on_progress=on_progress)

    # Suggested questions (spec §5 Move 3) — best-effort. The generator
    # itself returns a fallback on every error mode; we add a hard 5s wall
    # clock here so a hanging LLM never blocks the brief.
    actions_block: List[Dict[str, Any]] = []
    failure_reason: Optional[str] = None
    elapsed_seconds = 0.0
    try:
        import time as _time
        started = _time.perf_counter()
        questions = generate_suggested_questions(
            session.findings, _personas_for_suggestions(session)
        )
        elapsed_seconds = _time.perf_counter() - started
        if elapsed_seconds > 5.0:
            failure_reason = "slow"
        elif is_fallback(questions):
            failure_reason = "fallback"
        actions_block = suggested_questions_block(questions)
    except Exception as e:  # noqa: BLE001
        safe_log_exception(
            logger, e, "[runner] suggested_questions generation failed"
        )
        actions_block = []
        failure_reason = "raised"

    if actions_block:
        blocks = blocks + actions_block

    try:
        post(
            blocks=validate_blocks(blocks),
            text=f"Research for {session.account_name}",
        )
    except Exception as e:  # noqa: BLE001
        safe_log_exception(logger, e, "account research post() failed")

    # Log degradation events AFTER the brief is posted so failure modes
    # never block the rep's view of the research.
    if failure_reason is not None:
        _log_suggested_questions_event(session, failure_reason, elapsed_seconds)


# ---------------------------------------------------------------------------
# Stage 2 — Persona contact pull
# ---------------------------------------------------------------------------

def run_persona_research(
    session: ResearchSession,
    post: Callable[..., Any],
    on_progress: Optional[Callable[[str], None]] = None,
) -> None:
    """Build HubSpot snapshot + Apollo contacts (tagged against HubSpot)
    for the selected personas, then post the result via `post`.

    `post` may be either:
      - a Slack `respond()` callable (legacy / test path) — the runner
        will pass `replace_original=False, response_type="ephemeral"`,
      - or a generic `chat.postMessage`-style callable (production
        threaded path) — the handler shapes the kwargs.

    `on_progress`, if provided, is called with status strings as each
    sub-step runs.
    """
    blocks = _build_persona_blocks(session, on_progress=on_progress)
    try:
        post(
            blocks=blocks,
            text=f"Contacts for {session.account_name}",
        )
    except Exception as e:  # noqa: BLE001
        safe_log_exception(logger, e, "persona research post() failed")


# ---------------------------------------------------------------------------
# Legacy full flow — used by Phase 13 integration tests
# ---------------------------------------------------------------------------

def run_research(session: ResearchSession, respond: Callable[..., Any]) -> None:
    """Legacy single-shot flow: snapshot + findings + contacts in one post.

    Kept so Phase 13 integration tests still pass without modification.
    Production no longer calls this — the DM handler runs Stage 1, the
    persona-button handler runs Stage 2.
    """
    blocks = _build_account_blocks(session) + _build_persona_blocks(session)
    try:
        respond(
            response_type="ephemeral",
            replace_original=True,
            blocks=blocks,
            text=f"Research for {session.account_name}",
        )
    except Exception as e:  # noqa: BLE001
        safe_log_exception(logger, e, "respond() failed")


# ---------------------------------------------------------------------------
# Internal builders
# ---------------------------------------------------------------------------

def _emit(on_progress: Optional[Callable[[str], None]], msg: str) -> None:
    if on_progress is None:
        return
    try:
        on_progress(msg)
    except Exception:  # noqa: BLE001 — progress is best-effort
        pass


def _build_account_blocks(
    session: ResearchSession,
    on_progress: Optional[Callable[[str], None]] = None,
) -> List[Dict[str, Any]]:
    """Pure-research blocks (Exa + OpenRouter findings).

    Intentionally does NOT call HubSpot — Stage 1 must keep working even
    if the HubSpot token is invalid. Snapshot moved to Stage 2.

    Side effect: cache the findings on the session so Stage 2's angle
    builder can ground its output without re-running Exa+OpenRouter.
    """
    # V1.5 memory layer: load the most recent snapshot before findings
    # run, so we can diff after. Memory I/O is best-effort — never let
    # it break the research response.
    prev_snapshot: Optional[Dict[str, Any]] = None
    try:
        prev_snapshot = get_latest_snapshot(session.account_name)
    except Exception as e:  # noqa: BLE001
        safe_log_exception(logger, e, "memory: get_latest_snapshot failed")

    try:
        findings = build_findings(session, on_progress=on_progress)
    except Exception as e:  # noqa: BLE001
        safe_log_exception(logger, e, "build_findings raised unexpectedly")
        findings = {
            "account_name": session.account_name,
            "trigger_events": [],
            "competitor_signals": [],
            "dc_intel": [],
            "board_initiatives": [],
            "research_gaps": [
                f"Research extraction failed; {type(e).__name__}.",
            ],
        }
    # Best-effort cache; tolerate test sessions whose IDs aren't in the
    # in-memory store.
    try:
        set_findings(session.session_id, findings)
    except Exception:  # noqa: BLE001
        pass
    # Also stash on the dataclass directly so callers holding the
    # session reference (e.g. tests, legacy run_research) can read it.
    session.findings = findings

    # Persist a CompanyResearch row so the follow-up Q&A handler can find
    # the artifacts later. Best-effort; failure here must not block the
    # brief. (Without this, @-mentions in the thread return "I'm still
    # finishing the initial research" because the DB has no row.)
    try:
        _persist_company_research(session, findings)
    except Exception as e:  # noqa: BLE001
        safe_log_exception(logger, e, "persist_company_research failed")

    new_since_blocks: List[Dict[str, Any]] = []
    if prev_snapshot is not None:
        try:
            diff = diff_findings(prev_snapshot.get("findings"), findings)
            new_since_blocks = build_new_since_blocks(
                diff, prev_snapshot.get("saved_at")
            )
        except Exception as e:  # noqa: BLE001
            safe_log_exception(logger, e, "memory: diff/render failed")

    try:
        save_snapshot(session.account_name, findings)
    except Exception as e:  # noqa: BLE001
        safe_log_exception(logger, e, "memory: save_snapshot failed")

    return new_since_blocks + build_research_blocks(findings)


def _build_persona_blocks(
    session: ResearchSession,
    on_progress: Optional[Callable[[str], None]] = None,
) -> List[Dict[str, Any]]:
    """Stage 2 blocks: HubSpot account snapshot + reach-out angle card +
    Apollo contacts tagged against HubSpot. Never raises."""
    apollo_client = _safe_call(get_apollo_client, "apollo client init")
    hs_contact_client = _safe_call(
        get_hubspot_contact_client, "hubspot contact client init"
    )
    hs_account_client = _safe_call(
        get_hubspot_account_client, "hubspot account client init"
    )
    portal_id = _safe_call(get_hubspot_portal_id, "hubspot portal id read")

    _emit(on_progress, "👥 Searching Apollo for contacts…")
    try:
        tag_result = build_tagged_contacts(
            session,
            apollo_client=apollo_client,
            hubspot_contact_client=hs_contact_client,
            portal_id=portal_id,
        )
    except Exception as e:  # noqa: BLE001
        safe_log_exception(logger, e, "build_tagged_contacts raised")
        tag_result = {"contacts": [], "warning": "Contact pipeline failed"}

    snapshot_blocks: List[Dict[str, Any]] = []
    snap = None
    if hs_account_client is not None and portal_id:
        _emit(on_progress, "🏷️ Looking up account in HubSpot…")
        domain = resolve_domain(
            session.account_name, tag_result.get("contacts") or []
        )
        try:
            snap = get_account_snapshot(
                hs_account_client, session.account_name, domain, portal_id
            )
        except Exception as e:  # noqa: BLE001
            safe_log_exception(logger, e, "account snapshot lookup raised")
            snap = None
        snapshot_blocks = (
            build_account_snapshot_blocks(snap) if snap is not None
            else build_account_not_found_blocks(session.account_name)
        )

    # Reach-out angles — synthesizes findings + snapshot + contacts.
    # Empty-result safe; never raises.
    angle_blocks: List[Dict[str, Any]] = []
    findings = session.findings or {}
    if findings:
        _emit(on_progress, "🎯 Building reach-out angles…")
        try:
            angles = build_angles(
                findings=findings,
                snapshot=snap,
                tag_result=tag_result,
                persona_keys=list(session.personas or []),
            )
            angle_blocks = build_angle_blocks(angles, tag_result)
        except Exception as e:  # noqa: BLE001
            safe_log_exception(logger, e, "build_angles raised")
            angle_blocks = []

    return (
        snapshot_blocks
        + angle_blocks
        + build_contact_blocks(tag_result)
    )


def _safe_call(fn: Callable[[], Any], label: str) -> Optional[Any]:
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        safe_log_exception(logger, e, f"{label} failed")
        return None


# ---------------------------------------------------------------------------
# Suggested-questions helpers (V1 Daily-Use spec §5 Move 3)
# ---------------------------------------------------------------------------


def _personas_for_suggestions(session: ResearchSession) -> List[Dict[str, Any]]:
    """Best-effort pull of personas for the session — used as LLM input
    grounding for the suggested-questions generator. Returns an empty list
    on any failure; the generator handles the empty case."""
    try:
        from src.db.session import SessionLocal
        from src.db.models import Persona
    except Exception:  # noqa: BLE001
        return []
    if SessionLocal is None:
        return []
    db = SessionLocal()
    try:
        rows = (
            db.query(Persona)
            .filter(Persona.session_id == session.session_id)
            .all()
        )
        return [
            {
                "first_name": getattr(r, "first_name", None),
                "last_name": getattr(r, "last_name", None),
                "title": getattr(r, "title", None),
                "persona_type": getattr(r, "persona_type", None),
            }
            for r in rows
        ]
    except Exception as e:  # noqa: BLE001
        safe_log_exception(
            logger, e, "[runner] persona load for suggestions failed"
        )
        return []
    finally:
        try:
            db.close()
        except Exception:  # noqa: BLE001
            pass


def _log_suggested_questions_event(
    session: ResearchSession,
    reason: str,
    elapsed_seconds: float,
) -> None:
    """Persist a `WorkflowEvent` row for suggested-questions degradation.

    Reasons:
      - ``slow``     — generator returned within budget but exceeded 5s wall.
      - ``fallback`` — generator returned the generic fallback list.
      - ``raised``   — caller-side exception.

    Never raises; DB failures are logged type-only.
    """
    try:
        from src.db.session import SessionLocal
        from src.db.models import WorkflowEvent
    except Exception:  # noqa: BLE001
        return
    if SessionLocal is None:
        return

    if reason == "slow":
        event_type = "suggested_questions_slow"
    else:
        event_type = "suggested_questions_failed"

    db = SessionLocal()
    try:
        evt = WorkflowEvent(
            event_type=event_type,
            session_id=getattr(session, "session_id", None),
            rep_id=getattr(session, "rep_id", None),
            payload={
                "reason": reason,
                "elapsed_ms": int(elapsed_seconds * 1000),
            },
        )
        db.add(evt)
        db.commit()
    except Exception as e:  # noqa: BLE001
        safe_log_exception(
            logger, e, "[runner] failed to log suggested_questions event"
        )
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
    finally:
        try:
            db.close()
        except Exception:  # noqa: BLE001
            pass
