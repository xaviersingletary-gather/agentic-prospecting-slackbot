"""Natural-language DM entry point — V1.5 (replaces /research slash command).

Covers:
- Conversational filler is stripped before extraction.
- Bare account name passes through unchanged.
- Bot/edit messages are ignored.
- Empty / unintelligible text gets a usage-hint reply, no session created.
- A valid DM creates a session and posts the persona-checkbox card.
"""
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _reset_sessions():
    from src.research import sessions
    sessions._reset_for_tests()
    yield
    sessions._reset_for_tests()


def _msg(text, user="U1", bot_id=None, subtype=None):
    m = {"text": text, "user": user}
    if bot_id is not None:
        m["bot_id"] = bot_id
    if subtype is not None:
        m["subtype"] = subtype
    return m


# ---------------------------------------------------------------------------
# Account name extraction
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("Kroger", "Kroger"),
    ("research Kroger", "Kroger"),
    ("Research Kroger", "Kroger"),
    ("can you research Kroger", "Kroger"),
    ("could you please look up Sysco Foods", "Sysco Foods"),
    ("tell me about Pepsi", "Pepsi"),
    ("hi can you research Walmart?", "Walmart"),
    ("pull research on Target", "Target"),
    ("who is Albertsons", "Albertsons"),
    ("what do you know about Costco", "Costco"),
    ("'Sysco Foods'", "Sysco Foods"),
])
def test_extract_account_name_strips_conversational_filler(text, expected):
    from src.handlers.dm_research import _extract_account_name
    assert _extract_account_name(text) == expected


def test_empty_text_returns_empty():
    from src.handlers.dm_research import _extract_account_name
    assert _extract_account_name("") == ""
    assert _extract_account_name("   ") == ""


# ---------------------------------------------------------------------------
# Handler behavior
# ---------------------------------------------------------------------------

def test_dm_with_account_name_creates_session_and_posts_persona_card():
    from src.handlers.dm_research import handle_research_dm
    from src.research.sessions import _SESSIONS

    say = MagicMock()
    handle_research_dm(message=_msg("research Kroger"), say=say)

    say.assert_called_once()
    kwargs = say.call_args.kwargs
    blocks = kwargs.get("blocks")
    assert isinstance(blocks, list) and blocks
    # 4 persona checkboxes rendered
    actions = next(b for b in blocks if b.get("type") == "actions")
    checkboxes = next(e for e in actions["elements"] if e.get("type") == "checkboxes")
    assert len(checkboxes["options"]) == 4
    # Run Research button present
    buttons = [e for e in actions["elements"] if e.get("type") == "button"]
    assert any(b.get("action_id") == "run_research" for b in buttons)
    # Session created
    assert len(_SESSIONS) == 1
    sess = list(_SESSIONS.values())[0]
    assert sess.account_name == "Kroger"
    assert sess.rep_id == "U1"


def test_bare_account_name_works_without_filler():
    from src.handlers.dm_research import handle_research_dm
    from src.research.sessions import _SESSIONS

    say = MagicMock()
    handle_research_dm(message=_msg("Sysco Foods"), say=say)

    sess = list(_SESSIONS.values())[0]
    assert sess.account_name == "Sysco Foods"


def test_bot_message_is_ignored():
    from src.handlers.dm_research import handle_research_dm
    from src.research.sessions import _SESSIONS

    say = MagicMock()
    handle_research_dm(message=_msg("research Kroger", bot_id="B123"), say=say)

    say.assert_not_called()
    assert _SESSIONS == {}


def test_message_edit_is_ignored():
    from src.handlers.dm_research import handle_research_dm
    from src.research.sessions import _SESSIONS

    say = MagicMock()
    handle_research_dm(
        message=_msg("research Kroger", subtype="message_changed"),
        say=say,
    )

    say.assert_not_called()
    assert _SESSIONS == {}


def test_clear_keyword_is_ignored():
    """The legacy `clear` thread-cleanup handler owns this keyword."""
    from src.handlers.dm_research import handle_research_dm
    from src.research.sessions import _SESSIONS

    say = MagicMock()
    handle_research_dm(message=_msg("clear"), say=say)

    say.assert_not_called()
    assert _SESSIONS == {}


def test_empty_text_replies_with_usage_hint_no_session():
    from src.handlers.dm_research import handle_research_dm
    from src.research.sessions import _SESSIONS

    say = MagicMock()
    # User sent only conversational filler that strips down to nothing
    handle_research_dm(message=_msg("can you research"), say=say)

    say.assert_called_once()
    text = say.call_args.kwargs.get("text", "")
    assert "account name" in text.lower() or "example" in text.lower()
    assert _SESSIONS == {}
