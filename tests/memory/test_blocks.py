"""V1.5 memory layer — Block Kit renderer tests."""
from __future__ import annotations

from src.memory.blocks import build_new_since_blocks


def _empty_diff():
    return {
        "trigger_events": [],
        "competitor_signals": [],
        "dc_intel": [],
        "board_initiatives": [],
    }


def _item(claim, url):
    return {"claim": claim, "source_url": url}


def test_empty_diff_returns_empty_list():
    assert build_new_since_blocks(_empty_diff(), "2026-04-30T12:00:00Z") == []


def test_renders_header_with_formatted_date():
    diff = _empty_diff()
    diff["trigger_events"] = [_item("c", "https://x.test/c")]
    blocks = build_new_since_blocks(diff, "2026-04-30T12:00:00Z")
    assert blocks[0]["type"] == "header"
    assert "🆕 New since" in blocks[0]["text"]["text"]
    assert "Apr 30, 2026" in blocks[0]["text"]["text"]


def test_falls_back_when_saved_at_unparseable():
    diff = _empty_diff()
    diff["trigger_events"] = [_item("c", "https://x.test/c")]
    blocks = build_new_since_blocks(diff, "garbage")
    assert "garbage" in blocks[0]["text"]["text"]


def test_falls_back_when_saved_at_missing():
    diff = _empty_diff()
    diff["trigger_events"] = [_item("c", "https://x.test/c")]
    blocks = build_new_since_blocks(diff, None)
    assert "earlier" in blocks[0]["text"]["text"]


def test_renders_only_sections_with_new_items():
    diff = _empty_diff()
    diff["trigger_events"] = [_item("a trigger", "https://x.test/t")]
    diff["board_initiatives"] = [_item("a board thing", "https://x.test/b")]
    blocks = build_new_since_blocks(diff, "2026-04-30T12:00:00Z")
    rendered = "\n".join(
        b.get("text", {}).get("text", "") for b in blocks if b["type"] == "section"
    )
    assert "Trigger events" in rendered
    assert "Board initiatives" in rendered
    assert "Competitor signals" not in rendered
    assert "DC / facility intel" not in rendered


def test_includes_source_link_for_each_item():
    diff = _empty_diff()
    diff["trigger_events"] = [_item("claim", "https://example.com/path")]
    blocks = build_new_since_blocks(diff, "2026-04-30T12:00:00Z")
    text = "\n".join(
        b.get("text", {}).get("text", "") for b in blocks if b["type"] == "section"
    )
    assert "<https://example.com/path|example.com>" in text


def test_safe_mrkdwn_strips_dangerous_chars_in_claim():
    diff = _empty_diff()
    diff["trigger_events"] = [
        _item("evil <https://attacker.test|click>", "https://safe.test/ok"),
    ]
    blocks = build_new_since_blocks(diff, "2026-04-30T12:00:00Z")
    text = "\n".join(
        b.get("text", {}).get("text", "") for b in blocks if b["type"] == "section"
    )
    # Claim text must have had `<`, `>`, `|` stripped.
    assert "attacker.test" in text  # the raw substring survives
    assert "<https://attacker.test" not in text
    assert "|click>" not in text


def test_overflow_indicator_when_more_than_max_items():
    diff = _empty_diff()
    diff["trigger_events"] = [
        _item(f"c{i}", f"https://x.test/{i}") for i in range(7)
    ]
    blocks = build_new_since_blocks(diff, "2026-04-30T12:00:00Z")
    text = "\n".join(
        b.get("text", {}).get("text", "") for b in blocks if b["type"] == "section"
    )
    assert "and 3 more" in text


def test_ends_with_divider_when_non_empty():
    diff = _empty_diff()
    diff["trigger_events"] = [_item("c", "https://x.test/c")]
    blocks = build_new_since_blocks(diff, "2026-04-30T12:00:00Z")
    assert blocks[-1]["type"] == "divider"
