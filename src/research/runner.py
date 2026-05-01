"""Research runner — orchestrates the full Account Research Bot v1 output.

Composition (top → bottom in Slack):
  1. HubSpot account snapshot block (or "not found" / omitted)
  2. 5-section research findings (Phase 11 — Exa + Claude)
  3. Tagged contact list (Apollo + HubSpot — Phases 7 + 13)

Every external dependency is soft-failed:
- Missing env vars yield None clients via the factory; the runner
  inspects each and degrades gracefully.
- Any exception in any module is caught at the runner boundary, logged
  via `safe_log_exception`, and surfaced in the rendered output as a
  warning banner or research_gap. **The runner never raises.**

Phase 9 back-compat: `build_placeholder_findings` is kept as a thin
delegating alias to `build_findings`. Phase 9 tests still import it.
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
    """Deprecated alias. Delegates to `build_findings`. Kept so existing
    callers (Phase 9 tests, handlers that imported the old name) keep
    working without modification.
    """
    return build_findings(session)


def run_research(session: ResearchSession, respond: Callable[..., Any]) -> None:
    """Build the full research dump and post it back to Slack.

    Always calls `respond` exactly once. Never raises.
    """
    blocks: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Build clients lazily — re-reads env each call so adding a Railway
    # variable takes effect on the next invocation.
    # ------------------------------------------------------------------
    apollo_client = _safe_call(get_apollo_client, "apollo client init")
    hs_contact_client = _safe_call(
        get_hubspot_contact_client, "hubspot contact client init"
    )
    hs_account_client = _safe_call(
        get_hubspot_account_client, "hubspot account client init"
    )
    portal_id = _safe_call(get_hubspot_portal_id, "hubspot portal id read")

    # ------------------------------------------------------------------
    # 1. Findings — Phase 11 pipeline. build_findings never raises.
    # ------------------------------------------------------------------
    try:
        findings = build_findings(session)
    except Exception as e:  # belt-and-braces — should not happen
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

    # ------------------------------------------------------------------
    # 2. Contacts — Apollo + HubSpot tagging
    # ------------------------------------------------------------------
    try:
        tag_result = build_tagged_contacts(
            session,
            apollo_client=apollo_client,
            hubspot_contact_client=hs_contact_client,
            portal_id=portal_id,
        )
    except Exception as e:  # noqa: BLE001
        safe_log_exception(logger, e, "build_tagged_contacts raised")
        tag_result = {
            "contacts": [],
            "warning": "Contact pipeline failed",
        }

    # ------------------------------------------------------------------
    # 3. HubSpot account snapshot — only when account client + portal id
    #    are present.
    # ------------------------------------------------------------------
    snapshot_blocks: List[Dict[str, Any]] = []
    if hs_account_client is not None and portal_id:
        domain = resolve_domain(session.account_name, tag_result.get("contacts") or [])
        try:
            snap = get_account_snapshot(
                hs_account_client, session.account_name, domain, portal_id
            )
        except Exception as e:  # belt-and-braces
            safe_log_exception(logger, e, "account snapshot lookup raised")
            snap = None

        if snap is not None:
            snapshot_blocks = build_account_snapshot_blocks(snap)
        else:
            snapshot_blocks = build_account_not_found_blocks(session.account_name)

    # ------------------------------------------------------------------
    # Compose
    # ------------------------------------------------------------------
    blocks.extend(snapshot_blocks)
    blocks.extend(build_research_blocks(findings))
    blocks.extend(build_contact_blocks(tag_result))

    try:
        respond(
            response_type="ephemeral",
            replace_original=True,
            blocks=blocks,
            text=f"Research for {session.account_name}",
        )
    except Exception as e:  # noqa: BLE001
        # Slack post itself failed — we cannot surface it via Slack.
        # Log and swallow so the runner contract (no raise) still holds.
        safe_log_exception(logger, e, "respond() failed")


def _safe_call(fn: Callable[[], Any], label: str) -> Optional[Any]:
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        safe_log_exception(logger, e, f"{label} failed")
        return None
