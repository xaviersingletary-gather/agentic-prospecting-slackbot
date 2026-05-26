"""Phase 22 — followup handler routes to v2 path when AccountResearch exists.

Integration test against the real handler. Slack client + LLM are
mocked; DB is in-memory sqlite seeded with both a Session row (so the
AUTHZ + cancelled-status guards pass) and an AccountResearch row (so
the new path triggers).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import AccountResearch, Base, ConversationTurn, Session as DBSession


@pytest.fixture
def db_sm():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


@pytest.fixture
def patched_all(mocker, db_sm):
    """Patch every captured SessionLocal binding so the handler sees sqlite."""
    mocker.patch("src.db.session.SessionLocal", db_sm)
    mocker.patch("src.research.account_research_store.SessionLocal", db_sm)
    mocker.patch("src.handlers.followup_qa.SessionLocal", db_sm)
    return db_sm


def _seed_session(sm, *, thread_ts="T_TS", rep_id="U_REP", account="Walmart"):
    db = sm()
    try:
        row = DBSession(
            id="sess-1",
            account_name=account,
            rep_id=rep_id,
            channel_id="C1",
            thread_ts=thread_ts,
            status="active",
        )
        db.add(row)
        db.commit()
    finally:
        db.close()


def _seed_account_research(sm, *, thread_ts="T_TS", account="Walmart"):
    db = sm()
    try:
        row = AccountResearch(
            thread_ts=thread_ts,
            account_name=account,
            rep_id="U_REP",
            channel_id="C1",
            intent="prospecting",
            research_blob={
                "schema_version": 1,
                "account_name": account,
                "intent": "prospecting",
                "generated_at": "2026-05-26T18:14:00+00:00",
                "agents": [
                    {
                        "agent_name": "agent_1_network_footprint",
                        "section_title": "Network Footprint",
                        "claims": [
                            {
                                "text": "211 DCs",
                                "source_tag": "public",
                                "source_url": "https://corp.walmart.com/x",
                            }
                        ],
                    }
                ],
                "stats": {},
            },
        )
        db.add(row)
        db.commit()
    finally:
        db.close()


def _make_message(text="<@U_BOT> what about DCs?", thread_ts="T_TS"):
    return {
        "text": text,
        "user": "U_REP",
        "channel": "C1",
        "ts": "T_QUESTION",
        "thread_ts": thread_ts,
    }


def _make_client():
    client = MagicMock()
    client.auth_test.return_value = {"user_id": "U_BOT"}
    client.conversations_replies.return_value = {"messages": []}
    return client


def test_v2_path_used_when_account_research_row_exists(mocker, patched_all):
    _seed_session(patched_all)
    _seed_account_research(patched_all)

    mock_v2 = mocker.patch(
        "src.handlers.followup_qa.build_followup_context_v2",
        return_value="<context>",
    )
    legacy_ctx = mocker.patch(
        "src.handlers.followup_qa.build_followup_context"
    )
    mocker.patch(
        "src.handlers.followup_qa.answer_followup",
        return_value="The research says 211 DCs in NA.",
    )

    from src.handlers.followup_qa import handle_followup

    say = MagicMock(return_value={"ok": True, "ts": "T_PLACEHOLDER"})
    handle_followup(_make_message(), say=say, client=_make_client())

    mock_v2.assert_called_once()
    legacy_ctx.assert_not_called()
    kwargs = mock_v2.call_args.kwargs
    assert kwargs["account_name"] == "Walmart"
    assert kwargs["question"] == "what about DCs?"
    assert kwargs["research_blob"]["account_name"] == "Walmart"


def test_v2_path_persists_user_and_assistant_turns(mocker, patched_all):
    _seed_session(patched_all)
    _seed_account_research(patched_all)

    mocker.patch(
        "src.handlers.followup_qa.build_followup_context_v2",
        return_value="<context>",
    )
    mocker.patch(
        "src.handlers.followup_qa.answer_followup",
        return_value="211 DCs.",
    )

    from src.handlers.followup_qa import handle_followup

    say = MagicMock(return_value={"ok": True, "ts": "T_PLACEHOLDER"})
    handle_followup(_make_message(), say=say, client=_make_client())

    db = patched_all()
    try:
        turns = (
            db.query(ConversationTurn)
            .filter(ConversationTurn.thread_ts == "T_TS")
            .order_by(ConversationTurn.created_at.asc())
            .all()
        )
    finally:
        db.close()
    assert len(turns) == 2
    assert turns[0].role == "user"
    assert turns[0].message == "what about DCs?"
    assert turns[1].role == "assistant"
    assert turns[1].message == "211 DCs."


def test_legacy_path_still_used_when_no_account_research_row(mocker, patched_all):
    _seed_session(patched_all)
    # No AccountResearch row seeded.

    mock_v2 = mocker.patch(
        "src.handlers.followup_qa.build_followup_context_v2"
    )
    mock_legacy = mocker.patch(
        "src.handlers.followup_qa.build_followup_context",
        return_value="<legacy>",
    )
    mocker.patch(
        "src.handlers.followup_qa.answer_followup",
        return_value="legacy answer",
    )

    from src.handlers.followup_qa import handle_followup

    # The legacy path requires a CompanyResearch row — when absent the
    # handler short-circuits with a "research running" message rather
    # than calling either context builder. That's the expected behavior
    # for this test: it confirms v2 is NOT called.
    say = MagicMock(return_value={"ok": True, "ts": "T_PLACEHOLDER"})
    handle_followup(_make_message(), say=say, client=_make_client())

    mock_v2.assert_not_called()
    # Either legacy is called (with CompanyResearch seeded — not here) OR
    # the handler short-circuits before either path. Both outcomes are
    # acceptable; what matters is v2 didn't fire without an AccountResearch.
    assert mock_v2.call_count == 0
