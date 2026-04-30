"""Slack action handler: 'Run Research' button.

Reads checkbox state from the action payload, authorizes the clicker
against the session's rep_id, validates ≥1 persona is selected, and
persists the selection. Phase 3 stops there; later phases will hook the
research kickoff into the same handler via the `kickoff_research` seam.
"""
from typing import Any, Awaitable, Callable, Dict, List

from src.research.sessions import get_session, update_personas
from src.security.session_auth import (
    UnauthorizedSessionError,
    assert_session_owner,
)


# Sentinel that future phases will replace with the real research kickoff.
# Kept patchable so the security test can assert it is *not* called when the
# clicker is unauthorized.
async def kickoff_research(session) -> None:  # pragma: no cover - placeholder
    return None


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


async def _maybe_await(value):
    if hasattr(value, "__await__"):
        return await value
    return value


async def handle_run_research_action(
    payload: Dict[str, Any],
    ack: Callable[..., Awaitable[Any]],
    respond: Callable[..., Awaitable[Any]],
) -> None:
    await _maybe_await(ack())

    session_id = _extract_session_id(payload)
    user_id = (payload.get("user") or {}).get("id", "")

    sess = get_session(session_id)
    if sess is None:
        await respond(
            text="Session expired — please run /research again.",
            response_type="ephemeral",
        )
        return

    try:
        assert_session_owner(sess.rep_id, user_id)
    except UnauthorizedSessionError:
        await respond(
            text="You don't own this research request — the rep who ran "
                 "/research is the only person who can submit it.",
            response_type="ephemeral",
        )
        return

    selected = _extract_selected_personas(payload)
    if not selected:
        await respond(
            text="Please select at least one persona to continue.",
            response_type="ephemeral",
        )
        return

    update_personas(session_id, selected)
    await kickoff_research(sess)
    await respond(
        text=(
            f"Research started for {sess.account_name} "
            f"({len(selected)} persona{'s' if len(selected) != 1 else ''})."
        ),
        response_type="ephemeral",
    )
