"""Security gate S1.3 (spec §1.3): session authorization.

Workspace membership is not authorization — anyone can click a button on
someone else's thread. Every state-mutating handler must verify
`action.user.id == session.rep_id` before mutating.

This phase introduces the reusable `assert_session_owner` primitive in
`src/security/`. Subsequent state-mutating handlers must call it.
"""
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _reset_sessions():
    from src.research import sessions

    sessions._reset_for_tests()
    yield
    sessions._reset_for_tests()


def test_assert_session_owner_passes_when_user_matches_rep():
    from src.security.session_auth import assert_session_owner

    # No exception
    assert_session_owner(session_rep_id="U_REP", action_user_id="U_REP")


def test_assert_session_owner_raises_when_user_differs():
    from src.security.session_auth import (
        UnauthorizedSessionError, assert_session_owner,
    )

    with pytest.raises(UnauthorizedSessionError):
        assert_session_owner(session_rep_id="U_REP", action_user_id="U_OTHER")


def test_assert_session_owner_raises_when_rep_id_empty():
    from src.security.session_auth import (
        UnauthorizedSessionError, assert_session_owner,
    )

    with pytest.raises(UnauthorizedSessionError):
        assert_session_owner(session_rep_id="", action_user_id="U_OTHER")


def _payload_for_run_button(session_id: str, user_id: str, selected=None):
    return {
        "type": "block_actions",
        "user": {"id": user_id, "username": "x"},
        "channel": {"id": "C123"},
        "actions": [
            {
                "action_id": "run_research",
                "block_id": f"persona_select::{session_id}",
                "type": "button",
                "value": session_id,
            }
        ],
        "state": {
            "values": {
                f"persona_select::{session_id}": {
                    "persona_checkboxes": {
                        "type": "checkboxes",
                        "selected_options": [
                            {"value": v, "text": {"type": "plain_text", "text": v}}
                            for v in (selected or [])
                        ],
                    }
                }
            }
        },
    }



def test_handler_refuses_to_mutate_when_user_is_not_session_owner():
    from src.research.sessions import create_session, get_session
    from src.handlers.persona_select import handle_run_research_action

    s = create_session(rep_id="U_REP_A", account_name="Kroger")
    # User B clicks the button on user A's session
    payload = _payload_for_run_button(
        s.session_id, user_id="U_REP_B", selected=["executive"],
    )

    ack = MagicMock()
    respond = MagicMock()
    handle_run_research_action(payload=payload, ack=ack, respond=respond)

    # State must not have been mutated
    assert get_session(s.session_id).personas == []
    # Caller should be told they're not authorized (ephemeral)
    text = (respond.call_args.kwargs.get("text") or "").lower()
    assert "own" in text or "authoriz" in text or "permission" in text



def test_handler_does_not_call_research_pipeline_when_unauthorized(mocker):
    """Even if a research-kickoff function existed, it must not run for an
    unauthorized clicker. We patch a sentinel and assert it was not called.
    """
    from src.research.sessions import create_session
    from src.handlers import persona_select as ps

    s = create_session(rep_id="U_REP_A", account_name="Kroger")
    payload = _payload_for_run_button(
        s.session_id, user_id="U_REP_B", selected=["executive"],
    )

    # Patch the module-level sentinel that any future research kickoff would
    # call. Phase 3 ships the auth check; later phases attach real work here.
    sentinel = mocker.patch.object(ps, "kickoff_research", create=True)

    ack = MagicMock()
    respond = MagicMock()
    ps.handle_run_research_action(payload=payload, ack=ack, respond=respond)

    sentinel.assert_not_called()
