"""Phase 2 — ICP override action handlers.

  - `icp_override_proceed` marks the session, then posts the intent
    capture card.
  - `icp_override_cancel` marks the session cancelled and posts a
    one-line acknowledgement.
  - Both handlers tolerate a missing/expired DB Session gracefully —
    they log by exception type and never raise.

Mirrors the test fixture style of phase16/test_intent_capture.py:
in-memory sqlite, SessionLocal patched on both `src.db.session` and
`src.main`. Slack Bolt is not invoked — we call the underscore-prefixed
handler functions directly.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, Session as DBSession


@pytest.fixture
def db_sessionmaker():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


@pytest.fixture
def patched_main(mocker, db_sessionmaker):
    mocker.patch("src.db.session.SessionLocal", db_sessionmaker)
    mocker.patch("src.main.SessionLocal", db_sessionmaker)
    return db_sessionmaker


def _seed_session(sm, *, session_id="sess-icp", account="Notion"):
    db = sm()
    try:
        row = DBSession(
            id=session_id,
            account_name=account,
            rep_id="U_REP",
            channel_id="C1",
        )
        db.add(row)
        db.commit()
    finally:
        db.close()


def _action_body(*, session_id: str, action_id: str, suffix: str):
    return {
        "actions": [
            {
                "action_id": action_id,
                "value": f"{session_id}::{suffix}",
            }
        ],
        "user": {"id": "U_REP"},
        "message": {"ts": "T_TS"},
    }


def test_proceed_posts_intent_card_and_marks_override(mocker, patched_main):
    _seed_session(patched_main)

    # is_account_ambiguous is called inside the handler — short-circuit it.
    mocker.patch(
        "src.handlers.intent_capture.is_account_ambiguous", return_value=None
    )
    # Capture the intent_capture_card call.
    intent_capture_card_mock = mocker.patch(
        "src.handlers.intent_capture.intent_capture_card",
        return_value=[{"type": "section", "text": {"type": "mrkdwn", "text": "intent"}}],
    )

    import src.main as main_mod

    ack = MagicMock()
    say = MagicMock()
    main_mod._v1_action_icp_override_proceed(
        ack=ack,
        body=_action_body(
            session_id="sess-icp",
            action_id="icp_override_proceed",
            suffix="proceed",
        ),
        say=say,
        client=MagicMock(),
    )

    ack.assert_called_once()
    intent_capture_card_mock.assert_called_once()
    say.assert_called_once()
    # The override flag is persisted on the session row.
    db = patched_main()
    try:
        row = db.query(DBSession).filter(DBSession.id == "sess-icp").first()
        assert (row.normalized_request or {}).get("icp_override") is True
    finally:
        db.close()


def test_cancel_marks_session_cancelled_and_acknowledges(patched_main):
    _seed_session(patched_main)

    import src.main as main_mod

    ack = MagicMock()
    say = MagicMock()
    main_mod._v1_action_icp_override_cancel(
        ack=ack,
        body=_action_body(
            session_id="sess-icp",
            action_id="icp_override_cancel",
            suffix="cancel",
        ),
        say=say,
        client=MagicMock(),
    )

    ack.assert_called_once()
    say.assert_called_once()
    db = patched_main()
    try:
        row = db.query(DBSession).filter(DBSession.id == "sess-icp").first()
        assert row.status == "cancelled"
    finally:
        db.close()


def test_proceed_with_missing_session_replies_expired(mocker, patched_main):
    # No session seeded — handler must say "expired" and not crash.
    mocker.patch(
        "src.handlers.intent_capture.is_account_ambiguous", return_value=None
    )
    intent_capture_card_mock = mocker.patch(
        "src.handlers.intent_capture.intent_capture_card",
        return_value=[],
    )

    import src.main as main_mod

    ack = MagicMock()
    say = MagicMock()
    main_mod._v1_action_icp_override_proceed(
        ack=ack,
        body=_action_body(
            session_id="sess-missing",
            action_id="icp_override_proceed",
            suffix="proceed",
        ),
        say=say,
        client=MagicMock(),
    )

    ack.assert_called_once()
    intent_capture_card_mock.assert_not_called()
    say.assert_called_once()
    assert "expired" in say.call_args.kwargs.get("text", "")


def test_cancel_with_missing_session_does_not_raise(patched_main):
    import src.main as main_mod

    ack = MagicMock()
    say = MagicMock()
    main_mod._v1_action_icp_override_cancel(
        ack=ack,
        body=_action_body(
            session_id="sess-missing",
            action_id="icp_override_cancel",
            suffix="cancel",
        ),
        say=say,
        client=MagicMock(),
    )

    ack.assert_called_once()
    say.assert_called_once()
