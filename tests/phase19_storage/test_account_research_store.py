"""Phase 19 (V1 May 26 spec) — AccountResearch + ConversationTurn store.

In-memory sqlite, no Slack, no LLM. Mirrors the pattern in
tests/phase16/test_session_foundation.py so the suite stays consistent.

Covers:
  - AccountResearch upsert (insert path)
  - AccountResearch upsert (update path — second call merges new fields)
  - get_account_research_by_thread_ts round-trip
  - has_research_for_thread true/false
  - ConversationTurn append + get_recent_turns order + limit
  - Role validation rejects bogus values
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base


@pytest.fixture
def db_engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def db_sessionmaker(db_engine):
    return sessionmaker(bind=db_engine, autoflush=False, autocommit=False)


@pytest.fixture
def patched_db(mocker, db_sessionmaker):
    # Patch on both the canonical location AND the store module's already-
    # imported binding so existing references see the in-memory sessionmaker.
    mocker.patch("src.db.session.SessionLocal", db_sessionmaker)
    mocker.patch(
        "src.research.account_research_store.SessionLocal", db_sessionmaker
    )
    return db_sessionmaker


# ---------------------------------------------------------------------------
# AccountResearch
# ---------------------------------------------------------------------------


def test_upsert_inserts_new_row(patched_db):
    from src.research.account_research_store import (
        get_account_research_by_thread_ts,
        upsert_account_research,
    )

    row_id = upsert_account_research(
        thread_ts="T1.000001",
        account_name="Walmart",
        rep_id="U_REP",
        channel_id="C1",
        intent="prospecting",
        research_blob={"agents": {"agent_1": {"sections": []}}},
    )
    assert row_id is not None

    row = get_account_research_by_thread_ts("T1.000001")
    assert row is not None
    assert row.account_name == "Walmart"
    assert row.intent == "prospecting"
    assert row.research_blob == {"agents": {"agent_1": {"sections": []}}}


def test_upsert_updates_existing_row_and_preserves_unspecified_fields(patched_db):
    from src.research.account_research_store import (
        get_account_research_by_thread_ts,
        upsert_account_research,
    )

    upsert_account_research(
        thread_ts="T2",
        account_name="Sysco",
        intent="prospecting",
    )
    # Second call only supplies a blob — intent must survive.
    upsert_account_research(
        thread_ts="T2",
        account_name="Sysco",
        research_blob={"agents": {"agent_7": {"sections": ["x"]}}},
    )
    row = get_account_research_by_thread_ts("T2")
    assert row.intent == "prospecting"
    assert row.research_blob == {"agents": {"agent_7": {"sections": ["x"]}}}


def test_get_account_research_returns_none_for_unknown_thread(patched_db):
    from src.research.account_research_store import (
        get_account_research_by_thread_ts,
    )

    assert get_account_research_by_thread_ts("T_NOPE") is None


def test_has_research_for_thread_reflects_row_presence(patched_db):
    from src.research.account_research_store import (
        has_research_for_thread,
        upsert_account_research,
    )

    assert has_research_for_thread("T3") is False
    upsert_account_research(thread_ts="T3", account_name="Kroger")
    assert has_research_for_thread("T3") is True


# ---------------------------------------------------------------------------
# ConversationTurn
# ---------------------------------------------------------------------------


def test_append_and_get_recent_turns_in_chronological_order(patched_db):
    from src.research.account_research_store import (
        append_conversation_turn,
        get_recent_turns,
    )

    append_conversation_turn(thread_ts="T4", role="user", message="msg-1")
    append_conversation_turn(thread_ts="T4", role="assistant", message="msg-2")
    append_conversation_turn(thread_ts="T4", role="user", message="msg-3")

    turns = get_recent_turns("T4")
    assert [t.message for t in turns] == ["msg-1", "msg-2", "msg-3"]
    assert [t.role for t in turns] == ["user", "assistant", "user"]


def test_get_recent_turns_respects_limit(patched_db):
    from src.research.account_research_store import (
        append_conversation_turn,
        get_recent_turns,
    )

    for i in range(25):
        append_conversation_turn(
            thread_ts="T5", role="user", message=f"msg-{i}"
        )
    turns = get_recent_turns("T5", limit=20)
    assert len(turns) == 20
    # Last 20 in insertion order — oldest of the kept slice is msg-5.
    assert turns[0].message == "msg-5"
    assert turns[-1].message == "msg-24"


def test_append_rejects_invalid_role(patched_db):
    from src.research.account_research_store import append_conversation_turn

    with pytest.raises(ValueError):
        append_conversation_turn(
            thread_ts="T6", role="system", message="nope"
        )


def test_append_returns_none_when_message_empty(patched_db):
    from src.research.account_research_store import append_conversation_turn

    assert (
        append_conversation_turn(thread_ts="T7", role="user", message="")
        is None
    )
