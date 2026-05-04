"""Slack action handler: 'Run Research' button.

Reads checkbox state from the action payload, authorizes the clicker
against the session's rep_id, validates ≥1 persona is selected, and
persists the selection, then runs Stage 2 (HubSpot snapshot + Apollo
contacts).

Threading: the persona card was posted in a DM thread by the DM handler.
When `client` is provided (production path), Stage 2 results post into
the same thread via `chat.postMessage(thread_ts=…)`. When absent (test
path), we fall back to `respond()` so the legacy contract still holds.

Sync by design — the legacy slack-bolt App is sync; AsyncApp is not used.
"""
from typing import Any, Callable, Dict, List, Optional

from src.research.runner import run_persona_research
from src.research.sessions import get_session, update_personas
from src.security.session_auth import (
    UnauthorizedSessionError,
    assert_session_owner,
)


def kickoff_research(
    session,
    post: Callable[..., Any],
    on_progress: Optional[Callable[[str], None]] = None,
) -> None:
    """Thin shim — kept patchable so security tests can assert it is not
    called for unauthorized clickers, and so future phases can swap the
    runner without touching the handler."""
    run_persona_research(session, post, on_progress=on_progress)


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


def _build_threaded_post_and_status(
    payload: Dict[str, Any],
    client: Any,
):
    """Build (post, update_status) callables that post Stage 2 results
    into the same thread as the persona card and rewrite a single
    progress message inline.

    Returns (None, None) if `client` is missing or the payload is
    missing the channel/message context. Caller falls back to `respond`.
    """
    if client is None:
        return None, None
    channel = (payload.get("channel") or {}).get("id")
    msg = payload.get("message") or {}
    thread_ts = msg.get("thread_ts") or msg.get("ts")
    if not channel or not thread_ts:
        return None, None

    account_name = ""

    # Post the initial status message; capture its ts for in-place updates.
    try:
        status_resp = client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=":hourglass_flowing_sand: *Pulling personas + contacts…*",
        )
    except Exception:  # noqa: BLE001
        status_resp = None
    status_ts = None
    if status_resp is not None:
        status_ts = (
            status_resp.get("ts")
            if isinstance(status_resp, dict)
            else getattr(status_resp, "data", {}).get("ts", None)
        )

    def update_status(line: str) -> None:
        if status_ts is None:
            return
        try:
            client.chat_update(
                channel=channel,
                ts=status_ts,
                text=f":hourglass_flowing_sand: *Pulling personas + contacts…*\n_{line}_",
            )
        except Exception:  # noqa: BLE001
            pass

    def post(blocks=None, text=None, **_ignored: Any) -> Any:
        try:
            return client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                blocks=blocks,
                text=text,
            )
        except Exception:  # noqa: BLE001 — fall through to respond fallback
            return None

    def finalize(account_label: str) -> None:
        if status_ts is None:
            return
        try:
            client.chat_update(
                channel=channel,
                ts=status_ts,
                text=f":white_check_mark: *Personas + contacts ready for {account_label}*",
            )
        except Exception:  # noqa: BLE001
            pass

    # Wrap update_status to also expose finalize via attribute access —
    # avoids returning a third callable.
    update_status.finalize = finalize  # type: ignore[attr-defined]
    return post, update_status


def handle_run_research_action(
    payload: Dict[str, Any],
    ack: Callable[..., Any],
    respond: Callable[..., Any],
    client: Any = None,
) -> None:
    ack()

    session_id = _extract_session_id(payload)
    user_id = (payload.get("user") or {}).get("id", "")

    sess = get_session(session_id)
    if sess is None:
        respond(
            text="Session expired — please DM the bot the account name again.",
            response_type="ephemeral",
        )
        return

    try:
        assert_session_owner(sess.rep_id, user_id)
    except UnauthorizedSessionError:
        respond(
            text="You don't own this research request — the rep who started "
                 "the research is the only person who can submit it.",
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

    post, update_status = _build_threaded_post_and_status(payload, client)
    if post is None:
        # Test / fallback path — legacy respond() contract.
        kickoff_research(sess, respond)
        return

    kickoff_research(sess, post, on_progress=update_status)
    # Mark the status message as done.
    finalize = getattr(update_status, "finalize", None)
    if callable(finalize):
        finalize(sess.account_name)
