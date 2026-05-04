"""Slack action handler: 'Run Research' button.

Reads checkbox state from the action payload, authorizes the clicker
against the session's rep_id, validates ≥1 persona is selected, and
persists the selection. Phase 3 stops there; later phases will hook the
research kickoff into the same handler via the `kickoff_research` seam.

Sync by design — the legacy slack-bolt App is sync; AsyncApp is not used.
"""
from typing import Any, Callable, Dict, List

from src.research.runner import run_persona_research
from src.research.sessions import get_session, update_personas
from src.security.session_auth import (
    UnauthorizedSessionError,
    assert_session_owner,
)


def kickoff_research(session, respond: Callable[..., Any]) -> None:
    """Thin shim — kept patchable so security tests can assert it is not
    called for unauthorized clickers. V1.5: only the persona contact
    pull runs here; account findings + snapshot already posted in the
    DM handler."""
    run_persona_research(session, respond)


def _extract_session_id(payload: Dict[str, Any]) -> str:
    for action in payload.get("actions") or []:
        if action.get("action_id") == "run_research":
            value = action.get("value")
            if value:
                return value
        block_id = action.get("block_id", "")
        if block_id.startswith("persona_select::"):
            return block_id.split("::", 1)[1]
    return ""


def _extract_selected_personas(payload: Dict[str, Any]) -> List[str]:
    state_values = (payload.get("state") or {}).get("values") or {}
    for block_values in state_values.values():
        for element in block_values.values():
            if element.get("type") == "checkboxes":
                return [
                    opt["value"]
                    for opt in (element.get("selected_options") or [])
                ]
    for action in payload.get("actions") or []:
        if action.get("action_id") == "persona_checkboxes":
            return [
                opt["value"]
                for opt in (action.get("selected_options") or [])
            ]
    return []


def handle_run_research_action(
    payload: Dict[str, Any],
    ack: Callable[..., Any],
    respond: Callable[..., Any],
) -> None:
    ack()

    session_id = _extract_session_id(payload)
    user_id = (payload.get("user") or {}).get("id", "")

    sess = get_session(session_id)
    if sess is None:
        respond(
            text="Session expired — please run /research again.",
            response_type="ephemeral",
        )
        return

    try:
        assert_session_owner(sess.rep_id, user_id)
    except UnauthorizedSessionError:
        respond(
            text="You don't own this research request — the rep who ran "
                 "/research is the only person who can submit it.",
            response_type="ephemeral",
        )
        return

    selected = _extract_selected_personas(payload)
    if not selected:
        respond(
            text="Please select at least one persona to continue.",
            response_type="ephemeral",
        )
        return

    update_personas(session_id, selected)
    kickoff_research(sess, respond)
