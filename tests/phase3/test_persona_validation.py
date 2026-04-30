"""Spec §1.3 — `Run Research` button validates persona selection.

Submitting with zero personas selected must respond with an inline error
and must NOT mutate the session's persona list.
"""
from unittest.mock import AsyncMock

import pytest


@pytest.fixture(autouse=True)
def _reset_sessions():
    from src.research import sessions

    sessions._reset_for_tests()
    yield
    sessions._reset_for_tests()


def _payload_for_run_button(session_id: str, user_id: str, selected_persona_values=None):
    """Build a Slack block_actions payload mimicking the 'Run Research' click."""
    selected = selected_persona_values or []
    return {
        "type": "block_actions",
        "user": {"id": user_id, "username": "rep"},
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
                            for v in selected
                        ],
                    }
                }
            }
        },
    }


@pytest.mark.asyncio
async def test_zero_personas_selected_responds_with_validation_error():
    from src.research.sessions import create_session, get_session
    from src.handlers.persona_select import handle_run_research_action

    s = create_session(rep_id="U_REP", account_name="Kroger")
    payload = _payload_for_run_button(s.session_id, user_id="U_REP", selected_persona_values=[])

    ack = AsyncMock()
    respond = AsyncMock()
    await handle_run_research_action(payload=payload, ack=ack, respond=respond)

    ack.assert_awaited()
    respond.assert_awaited()
    text = (respond.call_args.kwargs.get("text") or "").lower()
    assert "at least one persona" in text

    # Session must remain unmutated
    assert get_session(s.session_id).personas == []


@pytest.mark.asyncio
async def test_one_persona_selected_persists_to_session():
    from src.research.sessions import create_session, get_session
    from src.handlers.persona_select import handle_run_research_action

    s = create_session(rep_id="U_REP", account_name="Kroger")
    payload = _payload_for_run_button(
        s.session_id, user_id="U_REP",
        selected_persona_values=["vp_warehouse_ops"],
    )

    ack = AsyncMock()
    respond = AsyncMock()
    await handle_run_research_action(payload=payload, ack=ack, respond=respond)

    assert get_session(s.session_id).personas == ["vp_warehouse_ops"]


@pytest.mark.asyncio
async def test_all_four_personas_persist_to_session():
    from src.research.sessions import create_session, get_session
    from src.handlers.persona_select import handle_run_research_action

    s = create_session(rep_id="U_REP", account_name="Kroger")
    all_four = ["csco", "vp_warehouse_ops", "vp_inventory_planning", "sop_lead"]
    payload = _payload_for_run_button(
        s.session_id, user_id="U_REP", selected_persona_values=all_four,
    )

    ack = AsyncMock()
    respond = AsyncMock()
    await handle_run_research_action(payload=payload, ack=ack, respond=respond)

    assert set(get_session(s.session_id).personas) == set(all_four)


@pytest.mark.asyncio
async def test_unknown_session_id_responds_gracefully():
    from src.handlers.persona_select import handle_run_research_action

    payload = _payload_for_run_button("does-not-exist", user_id="U_REP",
                                      selected_persona_values=["csco"])

    ack = AsyncMock()
    respond = AsyncMock()
    await handle_run_research_action(payload=payload, ack=ack, respond=respond)

    text = (respond.call_args.kwargs.get("text") or "").lower()
    assert "session" in text  # "session expired" / "session not found" etc.
