"""Phase 19 — Slack handler routes thread replies on AccountResearch presence.

5 cases:
  1. New DM (no thread_ts) → intent capture path (NOT follow-up).
  2. Reply in a thread with NO AccountResearch row → ignored (legacy path).
  3. Reply in a thread WITH AccountResearch row, bot mentioned → handle_followup.
  4. Reply in a thread WITH AccountResearch row, bot NOT mentioned → no-op.
  5. Two different threads share research independently (no cross-thread leak).

External Slack / agents / followup handler are mocked. DB is in-memory
sqlite. We assert which path was taken by inspecting which mock was
called — never by string-matching log output.
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
    """Patch every external boundary `handle_research_dm` reaches into."""
    mocker.patch("src.db.session.SessionLocal", db_sessionmaker)
    mocker.patch(
        "src.research.account_research_store.SessionLocal", db_sessionmaker
    )

    # Stub out the legacy Session lookup so the new path is exercised in
    # isolation. Returning None forces dm_research to fall to the new
    # AccountResearch branch.
    mocker.patch(
        "src.handlers.dm_research.get_session_by_thread_ts",
        return_value=None,
    )
    mocker.patch(
        "src.handlers.dm_research.find_any_session_for_thread",
        return_value=None,
    )

    # No prior snapshot — diff-front-door branch is skipped.
    mocker.patch(
        "src.handlers.dm_research.should_show_diff_front_door",
        return_value=None,
    )

    # Disambig check returns "unambiguous" for these tests.
    mocker.patch(
        "src.handlers.dm_research.is_account_ambiguous", return_value=None
    )

    handle_followup = mocker.patch(
        "src.handlers.dm_research.handle_followup"
    )
    intent_capture_card = mocker.patch(
        "src.handlers.dm_research.intent_capture_card",
        return_value=[{"type": "section", "text": {"type": "mrkdwn", "text": "stub"}}],
    )

    mocker.patch(
        "src.handlers.dm_research.get_bot_user_id", return_value="U_BOT"
    )
    mocker.patch(
        "src.handlers.dm_research.is_bot_mentioned",
        side_effect=lambda text, bot_id: f"<@{bot_id}>" in text,
    )

    return {
        "sm": db_sessionmaker,
        "handle_followup": handle_followup,
        "intent_capture_card": intent_capture_card,
    }


def _msg(*, text: str, thread_ts: str = None, ts: str = "T_TS"):
    m = {
        "text": text,
        "user": "U_REP",
        "channel": "C1",
        "ts": ts,
    }
    if thread_ts:
        m["thread_ts"] = thread_ts
    return m


def test_new_dm_routes_to_intent_capture(patched):
    from src.handlers.dm_research import handle_research_dm

    say = MagicMock()
    handle_research_dm(_msg(text="Walmart"), say=say, client=MagicMock())

    patched["handle_followup"].assert_not_called()
    patched["intent_capture_card"].assert_called_once()


def test_thread_reply_without_account_research_is_ignored(patched):
    from src.handlers.dm_research import handle_research_dm

    say = MagicMock()
    handle_research_dm(
        _msg(text="<@U_BOT> what about DCs?", thread_ts="T_PARENT"),
        say=say,
        client=MagicMock(),
    )

    patched["handle_followup"].assert_not_called()
    patched["intent_capture_card"].assert_not_called()
    say.assert_not_called()


def test_thread_reply_with_account_research_and_bot_mention_routes_to_followup(
    patched,
):
    from src.handlers.dm_research import handle_research_dm
    from src.research.account_research_store import upsert_account_research

    upsert_account_research(thread_ts="T_PARENT", account_name="Walmart")

    say = MagicMock()
    handle_research_dm(
        _msg(text="<@U_BOT> what about DCs?", thread_ts="T_PARENT"),
        say=say,
        client=MagicMock(),
    )

    patched["handle_followup"].assert_called_once()
    patched["intent_capture_card"].assert_not_called()


def test_thread_reply_with_account_research_no_mention_is_noop(patched):
    from src.handlers.dm_research import handle_research_dm
    from src.research.account_research_store import upsert_account_research

    upsert_account_research(thread_ts="T_PARENT", account_name="Walmart")

    say = MagicMock()
    handle_research_dm(
        _msg(text="just thinking out loud", thread_ts="T_PARENT"),
        say=say,
        client=MagicMock(),
    )

    patched["handle_followup"].assert_not_called()
    patched["intent_capture_card"].assert_not_called()


def test_two_threads_isolated(patched):
    from src.handlers.dm_research import handle_research_dm
    from src.research.account_research_store import upsert_account_research

    upsert_account_research(thread_ts="T_A", account_name="Walmart")
    # T_B has no research row.

    say = MagicMock()
    handle_research_dm(
        _msg(text="<@U_BOT> q?", thread_ts="T_A"),
        say=say,
        client=MagicMock(),
    )
    assert patched["handle_followup"].call_count == 1

    handle_research_dm(
        _msg(text="<@U_BOT> q?", thread_ts="T_B"),
        say=say,
        client=MagicMock(),
    )
    # Still 1 — T_B has no AccountResearch and no legacy session.
    assert patched["handle_followup"].call_count == 1
