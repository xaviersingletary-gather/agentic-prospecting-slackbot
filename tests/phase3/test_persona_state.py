"""Spec §1.3 — Persona selection state stored in-memory keyed by session id.

V1 uses a process-local dict; V2 will move this to Redis.
"""
import pytest


@pytest.fixture(autouse=True)
def _reset_sessions():
    from src.research import sessions

    sessions._reset_for_tests()
    yield
    sessions._reset_for_tests()


def test_create_session_returns_unique_session_id():
    from src.research.sessions import create_session

    a = create_session(rep_id="U1", account_name="Kroger")
    b = create_session(rep_id="U1", account_name="Kroger")
    assert a.session_id != b.session_id


def test_create_session_records_rep_and_account():
    from src.research.sessions import create_session

    s = create_session(rep_id="U_REP", account_name="Kroger")
    assert s.rep_id == "U_REP"
    assert s.account_name == "Kroger"
    assert s.personas == []


def test_get_session_returns_stored_session():
    from src.research.sessions import create_session, get_session

    s = create_session(rep_id="U1", account_name="Kroger")
    fetched = get_session(s.session_id)
    assert fetched is not None
    assert fetched.session_id == s.session_id
    assert fetched.rep_id == "U1"


def test_get_session_returns_none_for_unknown_id():
    from src.research.sessions import get_session

    assert get_session("does-not-exist") is None


def test_update_personas_persists_selection():
    from src.research.sessions import create_session, get_session, update_personas

    s = create_session(rep_id="U1", account_name="Kroger")
    update_personas(s.session_id, ["executive", "operations_lead"])
    fetched = get_session(s.session_id)
    assert fetched.personas == ["executive", "operations_lead"]


def test_update_personas_on_unknown_session_returns_none():
    from src.research.sessions import update_personas

    assert update_personas("missing", ["executive"]) is None
