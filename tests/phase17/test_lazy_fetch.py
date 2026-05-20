"""Phase 17 — lazy contact fetch inside follow-up Q&A handler.

Covers:
- `_is_contact_question` classifies questions correctly.
- `_lazy_fetch_contacts` calls the Apollo + HubSpot pipeline and persists
  Persona rows when triggered.
- Failure modes (Apollo unavailable, persist failure) don't raise; the
  follow-up handler degrades gracefully.

External Apollo / HubSpot clients are mocked. DB is in-memory sqlite.
"""
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, Persona, Session as DBSession
from src.handlers.followup_qa import (
    _CONTACT_QUESTION_KEYWORDS,
    _is_contact_question,
    _lazy_fetch_contacts,
)


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
    mocker.patch("src.db.session.SessionLocal", db_sessionmaker)
    return db_sessionmaker


@pytest.fixture
def session_row(patched_db):
    sm = patched_db
    db = sm()
    try:
        row = DBSession(
            id="sess-1",
            account_name="Acme Corp",
            rep_id="U_REP",
            channel_id="C123",
            thread_ts="1700.000",
            status="active",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        # Detach so we can use it outside the session.
        return row
    finally:
        db.close()


# ---------------------------------------------------------------------------
# _is_contact_question
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "q,expected",
    [
        ("Who should I hit first?", True),
        ("who is the champion at Acme", True),
        ("Email the VP of Ops", True),
        ("what's the LinkedIn for the CSCO", True),
        ("Best person to talk to in CI?", True),
        ("Who's the decision maker?", True),
        ("Any people in DCs hiring?", True),
        ("Tell me about Acme's expansion plan", False),
        ("How many DCs do they have?", False),
        ("What trigger events are most relevant?", False),
        ("", False),
    ],
)
def test_is_contact_question(q, expected):
    assert _is_contact_question(q) is expected


def test_keyword_set_includes_critical_terms():
    for term in ("who", "champion", "linkedin", "decision maker"):
        assert term in _CONTACT_QUESTION_KEYWORDS


# ---------------------------------------------------------------------------
# _lazy_fetch_contacts
# ---------------------------------------------------------------------------


def test_lazy_fetch_persists_contacts(patched_db, session_row, mocker):
    fake_apollo = MagicMock()
    fake_apollo.search_contacts_by_company_and_titles.return_value = [
        {
            "first_name": "Pat",
            "last_name": "CI",
            "title": "Director of Continuous Improvement",
            "email": "pat@acme.com",
            "apollo_id": "APL-LAZY-1",
        }
    ]
    mocker.patch(
        "src.research.clients_factory.get_apollo_client",
        return_value=fake_apollo,
    )
    mocker.patch(
        "src.research.clients_factory.get_hubspot_contact_client",
        return_value=None,
    )
    mocker.patch(
        "src.research.clients_factory.get_hubspot_portal_id",
        return_value=None,
    )

    fake_client = MagicMock()
    count = _lazy_fetch_contacts(
        session_row, fake_client, "C123", "place.1", "thread.1"
    )
    assert count == 1

    db = patched_db()
    try:
        rows = db.query(Persona).filter(Persona.session_id == "sess-1").all()
        assert len(rows) == 1
        assert rows[0].apollo_id == "APL-LAZY-1"
        assert rows[0].persona_type == "TDM"
    finally:
        db.close()


def test_lazy_fetch_apollo_missing_returns_zero(patched_db, session_row, mocker):
    mocker.patch(
        "src.research.clients_factory.get_apollo_client",
        return_value=None,
    )
    mocker.patch(
        "src.research.clients_factory.get_hubspot_contact_client",
        return_value=None,
    )
    mocker.patch(
        "src.research.clients_factory.get_hubspot_portal_id",
        return_value=None,
    )

    fake_client = MagicMock()
    count = _lazy_fetch_contacts(
        session_row, fake_client, "C123", None, "thread.1"
    )
    assert count == 0
    db = patched_db()
    try:
        assert db.query(Persona).count() == 0
    finally:
        db.close()


def test_lazy_fetch_apollo_raises_is_swallowed(patched_db, session_row, mocker):
    boom = MagicMock()
    boom.search_contacts_by_company_and_titles.side_effect = RuntimeError("apollo down")
    mocker.patch(
        "src.research.clients_factory.get_apollo_client",
        return_value=boom,
    )
    mocker.patch(
        "src.research.clients_factory.get_hubspot_contact_client",
        return_value=None,
    )
    mocker.patch(
        "src.research.clients_factory.get_hubspot_portal_id",
        return_value=None,
    )

    fake_client = MagicMock()
    count = _lazy_fetch_contacts(
        session_row, fake_client, "C123", None, "thread.1"
    )
    assert count == 0


def test_lazy_fetch_posts_placeholder_status(patched_db, session_row, mocker):
    mocker.patch(
        "src.research.clients_factory.get_apollo_client",
        return_value=None,
    )
    mocker.patch(
        "src.research.clients_factory.get_hubspot_contact_client",
        return_value=None,
    )
    mocker.patch(
        "src.research.clients_factory.get_hubspot_portal_id",
        return_value=None,
    )
    fake_client = MagicMock()
    _lazy_fetch_contacts(
        session_row, fake_client, "C123", "place.1", "thread.1"
    )
    # chat_update should be called with the contacts placeholder.
    chat_update_calls = fake_client.chat_update.call_args_list
    assert any(
        "Pulling contacts" in (call.kwargs.get("text") or "")
        for call in chat_update_calls
    )
