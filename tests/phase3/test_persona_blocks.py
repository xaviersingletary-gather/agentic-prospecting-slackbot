"""Spec §1.3 — Block Kit message with the four persona checkboxes."""


def _block_by_type(blocks, block_type):
    return [b for b in blocks if b.get("type") == block_type]


def test_blocks_contain_four_persona_options():
    from src.research.persona_blocks import build_persona_select_blocks

    blocks = build_persona_select_blocks(account_name="Kroger", session_id="sess123")
    actions = _block_by_type(blocks, "actions")
    assert actions, "expected an actions block"
    checkboxes = [
        e for e in actions[0]["elements"] if e.get("type") == "checkboxes"
    ]
    assert len(checkboxes) == 1
    options = checkboxes[0]["options"]
    assert len(options) == 4, f"expected 4 persona checkboxes, got {len(options)}"


def test_persona_options_have_correct_values():
    from src.research.persona_blocks import build_persona_select_blocks

    blocks = build_persona_select_blocks(account_name="Kroger", session_id="sess123")
    actions = _block_by_type(blocks, "actions")
    checkboxes = next(
        e for e in actions[0]["elements"] if e.get("type") == "checkboxes"
    )
    values = {opt["value"] for opt in checkboxes["options"]}
    assert values == {"csco", "vp_warehouse_ops", "vp_inventory_planning", "sop_lead"}


def test_persona_option_labels_match_spec():
    from src.research.persona_blocks import build_persona_select_blocks

    blocks = build_persona_select_blocks(account_name="Kroger", session_id="sess123")
    actions = _block_by_type(blocks, "actions")
    checkboxes = next(
        e for e in actions[0]["elements"] if e.get("type") == "checkboxes"
    )
    labels_by_value = {opt["value"]: opt["text"]["text"] for opt in checkboxes["options"]}
    assert "Chief Supply Chain" in labels_by_value["csco"]
    assert "Warehouse" in labels_by_value["vp_warehouse_ops"]
    assert "Inventory" in labels_by_value["vp_inventory_planning"]
    assert "S&OP" in labels_by_value["sop_lead"]


def test_blocks_contain_run_research_button_with_session_id():
    from src.research.persona_blocks import build_persona_select_blocks

    blocks = build_persona_select_blocks(account_name="Kroger", session_id="sess123")
    actions = _block_by_type(blocks, "actions")
    buttons = [e for e in actions[0]["elements"] if e.get("type") == "button"]
    assert len(buttons) == 1
    assert buttons[0]["action_id"] == "run_research"
    assert buttons[0]["value"] == "sess123"


def test_block_id_carries_session_id():
    from src.research.persona_blocks import build_persona_select_blocks

    blocks = build_persona_select_blocks(account_name="Kroger", session_id="sess123")
    actions = _block_by_type(blocks, "actions")
    assert "sess123" in actions[0]["block_id"]


def test_account_name_is_safe_mrkdwn():
    from src.research.persona_blocks import build_persona_select_blocks

    poisoned = "Evil<https://attacker.com|click>Co"
    blocks = build_persona_select_blocks(account_name=poisoned, session_id="s1")
    rendered = " ".join(
        b.get("text", {}).get("text", "") if isinstance(b.get("text"), dict)
        else ""
        for b in blocks
    )
    for ch in "<>|":
        assert ch not in rendered
