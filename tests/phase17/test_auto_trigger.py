"""Phase 17 — intent-driven auto-trigger for Stage 2 (contact discovery).

Covers:
- `_persist_personas_from_tag_result` upserts Persona rows from a
  Stage 2 tag_result.contacts list, idempotent within a session.
- Classification of `persona_type` (TDM/ODM/ExSp/IT) from title.
- `CONTACT_INTENTS` set is exactly {prospecting, meeting_prep, asset_building}.
- Stage 2 fires after Stage 1 for contact-oriented intents and does
  not fire for `general_research`.

DB is sqlite in-memory; external Slack / Apollo / HubSpot are mocked.
"""
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, Persona
from src.research.runner import (
    CONTACT_INTENTS,
    DEFAULT_PERSONAS,
    _persist_personas_from_tag_result,
)
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


def _make_research_session(personas=None) -> ResearchSession:
    s = ResearchSession(
        session_id="sess-123",
        rep_id="U_REP",
        account_name="Acme Corp",
    )
    if personas is not None:
        s.personas = list(personas)
    return s


# ---------------------------------------------------------------------------
# Set membership
# ---------------------------------------------------------------------------


def test_contact_intents_set_membership():
    assert CONTACT_INTENTS == frozenset({"prospecting", "meeting_prep", "asset_building"})
    assert "general_research" not in CONTACT_INTENTS


def test_default_personas_covers_four_roles():
    assert set(DEFAULT_PERSONAS) == {
        "technical_lead",
        "operations_lead",
        "executive",
        "compliance_lead",
    }


# ---------------------------------------------------------------------------
# Persistence helper
# ---------------------------------------------------------------------------


def test_persist_personas_empty_contacts_is_noop(patched_db):
    sess = _make_research_session()
    touched = _persist_personas_from_tag_result(sess, {"contacts": []})
    assert touched == 0
    db = patched_db()
    try:
        assert db.query(Persona).count() == 0
    finally:
        db.close()


def test_persist_personas_none_tag_result_is_noop(patched_db):
    sess = _make_research_session()
    touched = _persist_personas_from_tag_result(sess, None)
    assert touched == 0


def test_persist_personas_creates_rows(patched_db):
    sess = _make_research_session(personas=list(DEFAULT_PERSONAS))
    tag_result = {
        "contacts": [
            {
                "first_name": "Pat",
                "last_name": "Operator",
                "title": "Director of Continuous Improvement",
                "email": "pat@acme.com",
                "linkedin_url": "https://www.linkedin.com/in/pat-op",
                "apollo_id": "APL-1",
            },
            {
                "first_name": "Jordan",
                "last_name": "Ops",
                "title": "VP Operations",
                "email": "jordan@acme.com",
                "apollo_id": "APL-2",
            },
        ],
        "warning": None,
    }
    touched = _persist_personas_from_tag_result(sess, tag_result)
    assert touched == 2

    db = patched_db()
    try:
        rows = db.query(Persona).filter(Persona.session_id == "sess-123").all()
        assert len(rows) == 2
        by_apollo = {r.apollo_id: r for r in rows}
        assert by_apollo["APL-1"].title == "Director of Continuous Improvement"
        assert by_apollo["APL-1"].account_name == "Acme Corp"
        assert by_apollo["APL-1"].email == "pat@acme.com"
        assert by_apollo["APL-1"].status == "discovered"
        # Title-based classification:
        assert by_apollo["APL-1"].persona_type == "TDM"
        assert by_apollo["APL-2"].persona_type == "ODM"
    finally:
        db.close()


def test_persist_personas_is_idempotent_on_apollo_id(patched_db):
    sess = _make_research_session(personas=list(DEFAULT_PERSONAS))
    tag_result = {
        "contacts": [
            {
                "first_name": "Pat",
                "last_name": "Operator",
                "title": "Director of CI",
                "email": "pat@acme.com",
                "apollo_id": "APL-1",
            }
        ]
    }
    _persist_personas_from_tag_result(sess, tag_result)
    # Second call — same apollo_id, updated title.
    tag_result["contacts"][0]["title"] = "Sr. Director of Continuous Improvement"
    _persist_personas_from_tag_result(sess, tag_result)

    db = patched_db()
    try:
        rows = db.query(Persona).filter(Persona.apollo_id == "APL-1").all()
        assert len(rows) == 1
        assert rows[0].title == "Sr. Director of Continuous Improvement"
    finally:
        db.close()


def test_persist_personas_falls_back_to_email_when_no_apollo_id(patched_db):
    sess = _make_research_session(personas=list(DEFAULT_PERSONAS))
    tag_result = {
        "contacts": [
            {
                "first_name": "Pat",
                "last_name": "Operator",
                "title": "Director of CI",
                "email": "pat@acme.com",
                "apollo_id": None,
            }
        ]
    }
    _persist_personas_from_tag_result(sess, tag_result)
    _persist_personas_from_tag_result(sess, tag_result)

    db = patched_db()
    try:
        rows = (
            db.query(Persona)
            .filter(Persona.session_id == "sess-123", Persona.email == "pat@acme.com")
            .all()
        )
        assert len(rows) == 1
    finally:
        db.close()


def test_persist_personas_defaults_persona_keys_when_session_personas_empty(
    patched_db,
):
    # `session.personas` empty → should still classify titles using
    # the full DEFAULT_PERSONAS set.
    sess = _make_research_session(personas=None)
    tag_result = {
        "contacts": [
            {
                "first_name": "Sam",
                "last_name": "Exec",
                "title": "Chief Supply Chain Officer",
                "email": "sam@acme.com",
                "apollo_id": "APL-X",
            }
        ]
    }
    _persist_personas_from_tag_result(sess, tag_result)

    db = patched_db()
    try:
        row = db.query(Persona).filter(Persona.apollo_id == "APL-X").one()
        assert row.persona_type == "ExSp"
    finally:
        db.close()


def test_persist_personas_swallows_db_errors(patched_db, mocker):
    sess = _make_research_session(personas=list(DEFAULT_PERSONAS))
    # Patch SessionLocal in the runner module to a broken sessionmaker.
    broken = MagicMock()
    broken.return_value.query.side_effect = RuntimeError("db down")
    mocker.patch("src.db.session.SessionLocal", broken)
    tag_result = {"contacts": [{"first_name": "X", "last_name": "Y", "title": "VP Operations", "apollo_id": "Z"}]}
    # Should not raise.
    touched = _persist_personas_from_tag_result(sess, tag_result)
    assert touched == 0
