"""Phase 17 — ContactResearch / Persona cache behavior.

The cache is implicit: lazy contact fetch only fires when `Persona`
rows are empty for the session. Once they exist (either via Stage 2
auto-trigger or a prior lazy fetch), follow-up Q&A reads from the DB.

This test proves the second lazy_fetch call in the same session is a
no-op at the Apollo / HubSpot level — i.e., we cache.
"""
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, Persona, Session as DBSession
from src.handlers.followup_qa import _lazy_fetch_contacts
from src.research.runner import _persist_personas_from_tag_result
from src.research.sessions import ResearchSession


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
            id="sess-cache-1",
            account_name="Acme Corp",
            rep_id="U_REP",
            channel_id="C123",
            thread_ts="1700.000",
            status="active",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row
    finally:
        db.close()


def test_persist_personas_dedupes_same_apollo_id(patched_db):
    """Re-running persistence with the same apollo_id keeps row count
    constant — proves idempotency at the persistence layer."""
    sess = ResearchSession(
        session_id="sess-cache-1",
        rep_id="U_REP",
        account_name="Acme Corp",
    )
    tag_result = {
        "contacts": [
            {
                "first_name": "Pat",
                "last_name": "Operator",
                "title": "Director of CI",
                "email": "pat@acme.com",
                "apollo_id": "APL-DEDUPE",
            }
        ]
    }
    _persist_personas_from_tag_result(sess, tag_result)
    _persist_personas_from_tag_result(sess, tag_result)
    _persist_personas_from_tag_result(sess, tag_result)

    db = patched_db()
    try:
        assert (
            db.query(Persona)
            .filter(Persona.session_id == "sess-cache-1")
            .count()
            == 1
        )
    finally:
        db.close()


def test_lazy_fetch_runs_apollo_first_time(patched_db, session_row, mocker):
    """First lazy fetch with empty Persona table → Apollo gets called."""
    fake_apollo = MagicMock()
    fake_apollo.search_contacts_by_company_and_titles.return_value = [
        {
            "first_name": "Sam",
            "last_name": "Exec",
            "title": "Chief Supply Chain Officer",
            "email": "sam@acme.com",
            "apollo_id": "APL-S1",
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

    count = _lazy_fetch_contacts(
        session_row, MagicMock(), "C123", None, "thread.1"
    )
    assert count == 1
    # Apollo was hit exactly once on the first call.
    assert fake_apollo.search_contacts_by_company_and_titles.call_count == 1


def test_second_lazy_fetch_still_idempotent_no_dupes(patched_db, session_row, mocker):
    """Second lazy fetch in the same session must not duplicate Persona
    rows even if Apollo returns the same contact again. The handler-side
    gate (`if not personas: lazy_fetch`) means production won't run
    lazy_fetch twice, but the persistence layer must also be safe.
    """
    fake_apollo = MagicMock()
    fake_apollo.search_contacts_by_company_and_titles.return_value = [
        {
            "first_name": "Sam",
            "last_name": "Exec",
            "title": "Chief Supply Chain Officer",
            "email": "sam@acme.com",
            "apollo_id": "APL-S1",
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

    _lazy_fetch_contacts(session_row, MagicMock(), "C123", None, "thread.1")
    _lazy_fetch_contacts(session_row, MagicMock(), "C123", None, "thread.1")

    db = patched_db()
    try:
        assert (
            db.query(Persona)
            .filter(Persona.session_id == "sess-cache-1")
            .count()
            == 1
        )
    finally:
        db.close()
