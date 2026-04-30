"""Wiring step — `/research [account]` slash command handler."""
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _reset_sessions():
    from src.research import sessions
    sessions._reset_for_tests()
    yield
    sessions._reset_for_tests()


def test_research_command_with_account_name_returns_persona_blocks():
    from src.handlers.research_command import handle_research_command

    ack = MagicMock()
    respond = MagicMock()
    handle_research_command(
        command={"user_id": "U_REP", "text": "Kroger"},
        ack=ack,
        respond=respond,
    )
    ack.assert_called()
    kwargs = respond.call_args.kwargs
    assert kwargs.get("response_type") == "ephemeral"
    blocks = kwargs.get("blocks")
    assert isinstance(blocks, list) and blocks
    # The persona-select actions block must be there
    actions = [b for b in blocks if b.get("type") == "actions"]
    assert actions


def test_research_command_creates_session_keyed_to_rep():
    from src.handlers.research_command import handle_research_command
    from src.research.sessions import get_session

    ack = MagicMock()
    respond = MagicMock()
    handle_research_command(
        command={"user_id": "U_REP", "text": "Kroger"},
        ack=ack,
        respond=respond,
    )
    # Session id is embedded in the actions block_id
    blocks = respond.call_args.kwargs["blocks"]
    actions_block = next(b for b in blocks if b.get("type") == "actions")
    session_id = actions_block["block_id"].split("::", 1)[1]

    sess = get_session(session_id)
    assert sess is not None
    assert sess.rep_id == "U_REP"
    assert sess.account_name == "Kroger"


def test_research_command_with_no_text_shows_usage_hint():
    from src.handlers.research_command import handle_research_command

    ack = MagicMock()
    respond = MagicMock()
    handle_research_command(
        command={"user_id": "U_REP", "text": ""},
        ack=ack,
        respond=respond,
    )
    text = (respond.call_args.kwargs.get("text") or "").lower()
    assert "/research" in text or "usage" in text
    # No blocks payload (just the hint)
    assert "blocks" not in respond.call_args.kwargs


def test_research_command_strips_whitespace_from_account_name():
    from src.handlers.research_command import handle_research_command
    from src.research.sessions import get_session

    ack = MagicMock()
    respond = MagicMock()
    handle_research_command(
        command={"user_id": "U_REP", "text": "  Kroger  "},
        ack=ack,
        respond=respond,
    )
    blocks = respond.call_args.kwargs["blocks"]
    actions_block = next(b for b in blocks if b.get("type") == "actions")
    session_id = actions_block["block_id"].split("::", 1)[1]
    assert get_session(session_id).account_name == "Kroger"
