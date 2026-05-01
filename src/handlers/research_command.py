"""Slack slash command: `/research [Account Name]`.

1. ack() within Slack's 3-second budget.
2. If account text missing → ephemeral usage hint.
3. Create a new ResearchSession keyed by the rep_id.
4. Respond ephemerally with the persona-select Block Kit (4 checkboxes
   + Run Research button).

Usage tracking (S1.5a) — we redact the raw command text before any log
line: only the parsed account name lands on disk.
"""
from typing import Any, Callable, Dict

from src.research.persona_blocks import build_persona_select_blocks
from src.research.sessions import create_session


def _parse_account_name(command: Dict[str, Any]) -> str:
    return (command.get("text") or "").strip()


def handle_research_command(
    command: Dict[str, Any],
    ack: Callable[..., Any],
    respond: Callable[..., Any],
) -> None:
    ack()

    account_name = _parse_account_name(command)
    if not account_name:
        respond(
            text="Usage: `/research [Account Name]` — e.g. `/research Kroger`",
            response_type="ephemeral",
        )
        return

    rep_id = command.get("user_id") or ""
    session = create_session(rep_id=rep_id, account_name=account_name)

    respond(
        response_type="ephemeral",
        blocks=build_persona_select_blocks(
            account_name=account_name,
            session_id=session.session_id,
        ),
        text=f"Pick personas for {account_name}",
    )
