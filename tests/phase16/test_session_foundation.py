"""Phase 1 (V1 Daily-Use) — session foundation.

Covers spec §5 Move 5 + §7 Success Criterion 20:
- `get_session_by_thread_ts(thread_ts, rep_id)` returns the right Session row
- `handle_research_dm` persists `thread_ts` + `channel_id` on the DB Session

DB is sqlite in-memory; we monkey-patch `SessionLocal` on both modules that
hold a reference. External Slack / agents are mocked — no real API calls.
"""
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, Session as DBSession


@pytest.fixture
def db_engine():
    """Fresh in-memory sqlite engine per test — full isolation, fast."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def db_sessionmaker(db_engine):
    return sessionmaker(bind=db_engine, autoflush=False, autocommit=False)


@pytest.fixture
def patched_db(mocker, db_sessionmaker):
    """Point both `src.db.session.SessionLocal` and any module that already
    imported it at our in-memory test sessionmaker.
    """
    mocker.patch("src.db.session.SessionLocal", db_sessionmaker)
    return db_sessionmaker


def _make_session_row(
    sm,
    *,
    rep_id: str,
    thread_ts: str,
    account_name: str = "Acme",
    status: str = "active",
    created_at: datetime = None,
    channel_id: str = "C123",
) -> str:
    """Insert a Session row, return its id."""
    db = sm()
    try:
        row = DBSession(
            account_name=account_name,
            rep_id=rep_id,
            channel_id=channel_id,
            status=status,
        )
        row.thread_ts = thread_ts
        if created_at is not None:
            row.created_at = created_at
        db.add(row)
        db.commit()
        return row.id
    finally:
        db.close()


# ---------------------------------------------------------------------------
# get_session_by_thread_ts
# ---------------------------------------------------------------------------


def test_lookup_returns_session_for_matching_thread_and_rep(patched_db):
    from src.research.sessions import get_session_by_thread_ts

    sid = _make_session_row(patched_db, rep_id="U1", thread_ts="1700000000.0001")

    result = get_session_by_thread_ts("1700000000.0001", "U1")

    assert result is not None
    assert result.id == sid
    assert result.rep_id == "U1"
    assert result.thread_ts == "1700000000.0001"


def test_lookup_returns_none_for_wrong_rep(patched_db):
    from src.research.sessions import get_session_by_thread_ts

    _make_session_row(patched_db, rep_id="U1", thread_ts="1700000000.0002")

    assert get_session_by_thread_ts("1700000000.0002", "U_OTHER") is None


def test_lookup_returns_none_for_unknown_thread_ts(patched_db):
    from src.research.sessions import get_session_by_thread_ts

    _make_session_row(patched_db, rep_id="U1", thread_ts="1700000000.0003")

    assert get_session_by_thread_ts("9999999999.9999", "U1") is None


def test_lookup_skips_cancelled_sessions(patched_db):
    from src.research.sessions import get_session_by_thread_ts

    _make_session_row(
        patched_db,
        rep_id="U1",
        thread_ts="1700000000.0004",
        status="cancelled",
    )

    assert get_session_by_thread_ts("1700000000.0004", "U1") is None


def test_lookup_returns_most_recent_if_multiple(patched_db):
    from src.research.sessions import get_session_by_thread_ts

    older = datetime.utcnow() - timedelta(hours=2)
    newer = datetime.utcnow()

    _make_session_row(
        patched_db,
        rep_id="U1",
        thread_ts="1700000000.0005",
        account_name="Older",
        created_at=older,
    )
    newer_id = _make_session_row(
        patched_db,
        rep_id="U1",
        thread_ts="1700000000.0005",
        account_name="Newer",
        created_at=newer,
    )

    result = get_session_by_thread_ts("1700000000.0005", "U1")

    assert result is not None
    assert result.id == newer_id
    assert result.account_name == "Newer"


# ---------------------------------------------------------------------------
# dm_research persistence
# ---------------------------------------------------------------------------


def test_dm_research_persists_thread_ts_and_channel(mocker, patched_db):
    """After `handle_research_dm` runs, the DB Session row must have
    `thread_ts == message.ts` and `channel_id == message.channel`
    (spec §7 Success Criterion 20).
    """
    # Patch the DB Session symbol on the handler module too — it imports
    # SessionLocal lazily inside the function, so the `src.db.session`
    # patch already covers it. But we also need to stop the downstream
    # research pipeline from actually running.
    mocker.patch("src.handlers.dm_research.run_account_research")
    mocker.patch(
        "src.handlers.dm_research.build_persona_select_blocks",
        return_value=[],
    )

    from src.handlers.dm_research import handle_research_dm
    from src.research import sessions as sessions_module

    sessions_module._reset_for_tests()

    say = MagicMock(return_value={"ts": "ignored-status-ts"})
    client = MagicMock()

    message = {
        "text": "Kroger",
        "user": "U_REP_1",
        "ts": "1700000123.4567",
        "channel": "D_CHAN_1",
    }

    handle_research_dm(message=message, say=say, client=client)

    # Verify a Session row landed with the right thread_ts + channel_id.
    db = patched_db()
    try:
        row = (
            db.query(DBSession)
            .filter(DBSession.rep_id == "U_REP_1")
            .first()
        )
    finally:
        db.close()

    assert row is not None
    assert row.thread_ts == "1700000123.4567"
    assert row.channel_id == "D_CHAN_1"
    assert row.account_name == "Kroger"
