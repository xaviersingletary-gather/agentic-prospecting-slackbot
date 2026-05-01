"""Minimal research runner — closes the loop from persona-select to a
formatted research dump posted back to Slack. The actual data source is
a stub today; Phase 10+ swaps it for real Exa/Apollo/HubSpot calls.
"""
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _reset_sessions():
    from src.research import sessions
    sessions._reset_for_tests()
    yield
    sessions._reset_for_tests()


def test_placeholder_findings_carry_session_account_name():
    from src.research.runner import build_placeholder_findings
    from src.research.sessions import create_session

    s = create_session(rep_id="U_REP", account_name="Kroger")
    s.personas = ["vp_warehouse_ops"]

    findings = build_placeholder_findings(s)
    assert findings["account_name"] == "Kroger"
    # All five sections are present so the formatter can render
    for key in (
        "trigger_events",
        "competitor_signals",
        "dc_intel",
        "board_initiatives",
        "research_gaps",
    ):
        assert key in findings


def test_placeholder_findings_record_personas_in_research_gaps():
    from src.research.runner import build_placeholder_findings
    from src.research.sessions import create_session

    s = create_session(rep_id="U_REP", account_name="Kroger")
    s.personas = ["vp_warehouse_ops", "csco"]

    gaps_text = " ".join(build_placeholder_findings(s)["research_gaps"]).lower()
    assert "vp_warehouse_ops" in gaps_text or "warehouse" in gaps_text
    assert "csco" in gaps_text


def test_run_research_posts_blocks_via_respond():
    from src.research.runner import run_research
    from src.research.sessions import create_session

    s = create_session(rep_id="U_REP", account_name="Kroger")
    s.personas = ["vp_warehouse_ops"]
    respond = MagicMock()

    run_research(s, respond)

    respond.assert_called_once()
    kwargs = respond.call_args.kwargs
    assert kwargs.get("response_type") == "ephemeral"
    assert isinstance(kwargs.get("blocks"), list) and kwargs["blocks"]
    # Account name lands in the rendered output
    rendered = " ".join(
        b.get("text", {}).get("text", "") if isinstance(b.get("text"), dict)
        else b.get("text", {}).get("text", "")
        if isinstance(b.get("text"), dict)
        else ""
        for b in kwargs["blocks"]
    )
    assert "Kroger" in rendered


def test_run_research_replaces_original_message():
    """Persona-select message should be replaced once research is done — the
    user shouldn't end up with two messages on the same thread."""
    from src.research.runner import run_research
    from src.research.sessions import create_session

    s = create_session(rep_id="U_REP", account_name="Kroger")
    respond = MagicMock()

    run_research(s, respond)

    assert respond.call_args.kwargs.get("replace_original") is True


def test_kickoff_research_delegates_to_runner(mocker):
    """The handler-side seam must be a thin shim — anyone importing
    src.handlers.persona_select.kickoff_research should reach the runner."""
    from src.handlers import persona_select as ps
    from src.research.sessions import create_session

    s = create_session(rep_id="U_REP", account_name="Kroger")
    spy = mocker.patch("src.handlers.persona_select.run_research")
    respond = MagicMock()

    ps.kickoff_research(s, respond)
    spy.assert_called_once_with(s, respond)


def test_handler_invokes_kickoff_research_on_happy_path(mocker):
    """Authorized clicker, ≥1 persona → kickoff_research fires with
    session + respond. Earlier tests cover the negative paths."""
    from src.handlers import persona_select as ps
    from src.research.sessions import create_session

    s = create_session(rep_id="U_REP", account_name="Kroger")
    payload = {
        "type": "block_actions",
        "user": {"id": "U_REP", "username": "rep"},
        "actions": [{
            "action_id": "run_research",
            "block_id": f"persona_select::{s.session_id}",
            "type": "button",
            "value": s.session_id,
        }],
        "state": {
            "values": {
                f"persona_select::{s.session_id}": {
                    "persona_checkboxes": {
                        "type": "checkboxes",
                        "selected_options": [
                            {"value": "csco",
                             "text": {"type": "plain_text", "text": "x"}},
                        ],
                    }
                }
            }
        },
    }

    spy = mocker.patch.object(ps, "kickoff_research")
    ack = MagicMock()
    respond = MagicMock()
    ps.handle_run_research_action(payload=payload, ack=ack, respond=respond)

    spy.assert_called_once()
    args = spy.call_args
    assert args.args[0].session_id == s.session_id
    # Either positional or keyword — accept both shapes
    passed_respond = (
        args.args[1] if len(args.args) > 1
        else args.kwargs.get("respond")
    )
    assert passed_respond is respond
