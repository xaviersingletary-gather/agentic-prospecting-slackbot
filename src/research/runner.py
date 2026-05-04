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
from src.research.sessions import ResearchSession
from src.security.exception_logger import safe_log_exception
from src.integrations.hubspot.account_snapshot import (
    build_account_not_found_blocks,
    build_account_snapshot_blocks,
    get_account_snapshot,
)

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
) -> None:
    """Build findings + HubSpot snapshot and post them.

    `post` is the Slack posting callable — `say` from the message handler
    is the typical caller. Always invoked exactly once. Never raises.
    """
    blocks = _build_account_blocks(session)
    try:
        post(
            blocks=blocks,
            text=f"Research for {session.account_name}",
        )
    except Exception as e:  # noqa: BLE001
        safe_log_exception(logger, e, "account research post() failed")


# ---------------------------------------------------------------------------
# Stage 2 — Persona contact pull
# ---------------------------------------------------------------------------

def run_persona_research(
    session: ResearchSession,
    respond: Callable[..., Any],
) -> None:
    """Build Apollo+HubSpot tagged contacts for the selected personas.

    Posts as an ephemeral block_actions response that does NOT replace
    the persona card — we want the rep to see both their selection AND
    the resulting contacts.
    """
    blocks = _build_persona_blocks(session)
    try:
        respond(
            response_type="ephemeral",
            replace_original=False,
            blocks=blocks,
            text=f"Contacts for {session.account_name}",
        )
    except Exception as e:  # noqa: BLE001
        safe_log_exception(logger, e, "persona research respond() failed")


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

def _build_account_blocks(session: ResearchSession) -> List[Dict[str, Any]]:
    """Pure-research blocks (Exa + OpenRouter findings).

    Intentionally does NOT call HubSpot — Stage 1 must keep working even
    if the HubSpot token is invalid. Snapshot moved to Stage 2.
    """
    try:
        findings = build_findings(session)
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
    return build_research_blocks(findings)


def _build_persona_blocks(session: ResearchSession) -> List[Dict[str, Any]]:
    """Stage 2 blocks: HubSpot account snapshot + Apollo contacts tagged
    against HubSpot. Never raises."""
    apollo_client = _safe_call(get_apollo_client, "apollo client init")
    hs_contact_client = _safe_call(
        get_hubspot_contact_client, "hubspot contact client init"
    )
    hs_account_client = _safe_call(
        get_hubspot_account_client, "hubspot account client init"
    )
    portal_id = _safe_call(get_hubspot_portal_id, "hubspot portal id read")

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
    if hs_account_client is not None and portal_id:
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

    return snapshot_blocks + build_contact_blocks(tag_result)


def _safe_call(fn: Callable[[], Any], label: str) -> Optional[Any]:
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        safe_log_exception(logger, e, f"{label} failed")
        return None
