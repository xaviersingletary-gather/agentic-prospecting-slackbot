"""
Phase 1 Tests — Slack Block Builders
"""
import pytest
from src.integrations.slack_blocks import confirmation_card, clarification_card


def test_confirmation_card_renders():
    blocks = confirmation_card(
        account_name="Nestlé",
        persona_filter=["TDM", "FS"],
        use_case_angle="food safety compliance",
        session_id="test-session-123",
    )
    assert len(blocks) > 0
    text_block = blocks[0]
    assert "Nestlé" in text_block["text"]["text"]
    assert "TDM" in text_block["text"]["text"]
    assert "food safety compliance" in text_block["text"]["text"]


def test_confirmation_card_has_action_buttons():
    blocks = confirmation_card("Nestlé", None, None, "session-abc")
    action_block = next((b for b in blocks if b["type"] == "actions"), None)
    assert action_block is not None
    action_ids = [e["action_id"] for e in action_block["elements"]]
    assert "confirm_intent" in action_ids
    assert "edit_intent" in action_ids


def test_confirmation_card_session_id_in_button_value():
    session_id = "test-session-xyz"
    blocks = confirmation_card("Nestlé", None, None, session_id)
    action_block = next(b for b in blocks if b["type"] == "actions")
    confirm_btn = next(e for e in action_block["elements"] if e["action_id"] == "confirm_intent")
    assert confirm_btn["value"] == session_id


def test_confirmation_card_null_persona_filter():
    blocks = confirmation_card("Nestlé", None, None, "session-1")
    text_block = blocks[0]
    assert "All personas" in text_block["text"]["text"]


def test_confirmation_card_null_angle():
    blocks = confirmation_card("Nestlé", ["TDM"], None, "session-1")
    text_block = blocks[0]
    assert "General outreach" in text_block["text"]["text"]


def test_clarification_card_renders():
    blocks = clarification_card("Which company did you mean?", "session-1")
    assert len(blocks) > 0
    text_block = blocks[0]
    assert "Which company" in text_block["text"]["text"]


def test_clarification_card_has_submit_button():
    blocks = clarification_card("Which company?", "session-1")
    action_block = next((b for b in blocks if b["type"] == "actions"), None)
    assert action_block is not None
    btn = action_block["elements"][0]
    assert btn["action_id"] == "submit_clarification"
