"""Phase 2 — new-account-in-existing-thread guard.

Scenarios:
  1. "@bot research Acme" inside a Walmart thread → nudge, NOT follow-up.
  2. "look up Sysco" (no mention) inside a Walmart thread → nudge.
  3. "what about their DC count?" inside a Walmart thread → follow-up
     when bot is mentioned, no nudge.
  4. Conversational reply with no mention → silent ignore (no nudge,
     no follow-up).
  5. Thread without research → no nudge (existing legacy path).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base


@pytest.fixture
def db_sessionmaker():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


@pytest.fixture
def patched(mocker, db_sessionmaker):
    mocker.patch("src.db.session.SessionLocal", db_sessionmaker)
    mocker.patch(
        "src.research.account_research_store.SessionLocal", db_sessionmaker
    )
    mocker.patch(
        "src.handlers.dm_research.get_session_by_thread_ts", return_value=None
    )
    mocker.patch(
        "src.handlers.dm_research.find_any_session_for_thread", return_value=None
    )
    mocker.patch(
        "src.handlers.dm_research.should_show_diff_front_door", return_value=None
    )
    mocker.patch(
        "src.handlers.dm_research.check_icp_fit", return_value=None
    )
    mocker.patch(
        "src.handlers.dm_research.is_account_ambiguous", return_value=None
    )

    handle_followup = mocker.patch(
        "src.handlers.dm_research.handle_followup"
    )
    mocker.patch(
        "src.handlers.dm_research.intent_capture_card",
        return_value=[{"type": "section", "text": {"type": "mrkdwn", "text": "x"}}],
    )
    mocker.patch(
        "src.handlers.dm_research.get_bot_user_id", return_value="U_BOT"
    )
    mocker.patch(
        "src.handlers.dm_research.is_bot_mentioned",
        side_effect=lambda text, bot_id: f"<@{bot_id}>" in text,
    )

    return {"handle_followup": handle_followup}


def _msg(text, thread_ts=None):
    m = {"text": text, "user": "U_REP", "channel": "C1", "ts": "T_TS"}
    if thread_ts:
        m["thread_ts"] = thread_ts
    return m


def _seed_research(thread_ts: str, account: str = "Walmart"):
    from src.research.account_research_store import upsert_account_research
    upsert_account_research(thread_ts=thread_ts, account_name=account)


def test_research_intent_with_mention_in_thread_triggers_nudge(patched):
    from src.handlers.dm_research import handle_research_dm

    _seed_research("T_PARENT")
    say = MagicMock()
    handle_research_dm(
        _msg("<@U_BOT> research Acme", thread_ts="T_PARENT"),
        say=say,
        client=MagicMock(),
    )

    patched["handle_followup"].assert_not_called()
    assert say.call_count == 1
    text = say.call_args.kwargs.get("text", "")
    assert "fresh DM" in text or "single account" in text


def test_research_intent_without_mention_in_thread_triggers_nudge(patched):
    from src.handlers.dm_research import handle_research_dm

    _seed_research("T_PARENT")
    say = MagicMock()
    handle_research_dm(
        _msg("look up Sysco", thread_ts="T_PARENT"),
        say=say,
        client=MagicMock(),
    )

    patched["handle_followup"].assert_not_called()
    assert say.call_count == 1


def test_conversational_followup_with_mention_routes_to_followup(patched):
    from src.handlers.dm_research import handle_research_dm

    _seed_research("T_PARENT")
    say = MagicMock()
    handle_research_dm(
        _msg("<@U_BOT> what about their DC count?", thread_ts="T_PARENT"),
        say=say,
        client=MagicMock(),
    )

    patched["handle_followup"].assert_called_once()
    say.assert_not_called()


def test_conversational_followup_without_mention_is_silent(patched):
    from src.handlers.dm_research import handle_research_dm

    _seed_research("T_PARENT")
    say = MagicMock()
    handle_research_dm(
        _msg("just thinking", thread_ts="T_PARENT"),
        say=say,
        client=MagicMock(),
    )

    patched["handle_followup"].assert_not_called()
    say.assert_not_called()


def test_thread_without_research_does_not_nudge(patched):
    from src.handlers.dm_research import handle_research_dm

    # No research seeded for T_PARENT.
    say = MagicMock()
    handle_research_dm(
        _msg("research Acme", thread_ts="T_PARENT"),
        say=say,
        client=MagicMock(),
    )

    patched["handle_followup"].assert_not_called()
    say.assert_not_called()
