"""Phase 23 — /research-bot-stats command tests.

Admin-gating, aggregation arithmetic, and Slack output shape.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from src.handlers.research_bot_stats import (
    build_stats_blocks,
    handle_stats_command,
)


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _entries(*pairs):
    """Build a fake `read_recent` list from (event_type, fields) pairs."""
    out = []
    for et, fields in pairs:
        entry = {"event_type": et, "timestamp": "2026-05-26T00:00:00Z"}
        entry.update(fields)
        out.append(entry)
    return out


def _text_of(blocks):
    return "\n".join(
        b.get("text", {}).get("text", "")
        for b in blocks
        if isinstance(b.get("text"), dict)
    )


def test_build_stats_blocks_counts_each_event_type():
    blocks = build_stats_blocks(
        _entries(
            ("new_query", {"total_ms": 2000, "errored_agents": []}),
            ("new_query", {"total_ms": 4000, "errored_agents": ["agent_5_shrink_compliance"]}),
            ("followup", {"latency_ms": 800, "used_v2_path": True}),
            ("followup", {"latency_ms": 1200, "used_v2_path": False}),
            ("agent_failure", {"agent_name": "agent_5_shrink_compliance"}),
            ("disambig", {"chosen": "Volvo Group"}),
        )
    )
    text = _text_of(blocks)
    assert "new_query`: 2" in text
    assert "followup`: 2" in text
    assert "agent_failure`: 1" in text
    assert "disambig`: 1" in text


def test_build_stats_blocks_computes_averages_and_v2_pct():
    blocks = build_stats_blocks(
        _entries(
            ("new_query", {"total_ms": 2000, "errored_agents": []}),
            ("new_query", {"total_ms": 4000, "errored_agents": []}),
            ("followup", {"latency_ms": 1000, "used_v2_path": True}),
            ("followup", {"latency_ms": 1000, "used_v2_path": False}),
        )
    )
    text = _text_of(blocks)
    assert "avg 3000ms" in text  # (2000 + 4000) / 2
    assert "avg 1000ms" in text  # (1000 + 1000) / 2
    assert "v2-path 50%" in text


def test_build_stats_blocks_top_errored_agents_visible():
    blocks = build_stats_blocks(
        _entries(
            ("new_query", {"total_ms": 1, "errored_agents": ["agent_5_shrink_compliance"] * 3}),
            ("new_query", {"total_ms": 1, "errored_agents": ["agent_4_customer_signals"]}),
        )
    )
    text = _text_of(blocks)
    assert "agent_5_shrink_compliance=3" in text
    assert "agent_4_customer_signals=1" in text


def test_build_stats_blocks_handles_empty_log():
    blocks = build_stats_blocks([])
    text = _text_of(blocks)
    assert "Last 0 events" in text
    assert "avg n/a" in text


# ---------------------------------------------------------------------------
# Slash-command handler
# ---------------------------------------------------------------------------


def test_handler_blocks_non_admin(mocker):
    mocker.patch(
        "src.handlers.research_bot_stats.is_admin", return_value=False
    )
    read_mock = mocker.patch(
        "src.handlers.research_bot_stats.read_recent"
    )

    ack = MagicMock()
    respond = MagicMock()
    handle_stats_command(
        payload={"user_id": "U_NOBODY"}, ack=ack, respond=respond
    )

    ack.assert_called_once()
    respond.assert_called_once()
    # Non-admin response is a flat refusal — no blocks, no info leak.
    kwargs = respond.call_args.kwargs
    assert kwargs.get("response_type") == "ephemeral"
    assert "not available" in kwargs.get("text", "").lower()
    read_mock.assert_not_called()


def test_handler_admin_gets_stats(mocker, tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_SLACK_USER_IDS", "U_ADMIN")

    # Seed a JSONL.
    log_path = tmp_path / "usage.jsonl"
    log_path.write_text(
        json.dumps(
            {
                "event_type": "new_query",
                "timestamp": "2026-05-26T00:00:00Z",
                "total_ms": 1234,
                "errored_agents": [],
            }
        )
        + "\n"
    )

    ack = MagicMock()
    respond = MagicMock()
    handle_stats_command(
        payload={"user_id": "U_ADMIN"},
        ack=ack,
        respond=respond,
        log_path=str(log_path),
    )

    ack.assert_called_once()
    kwargs = respond.call_args.kwargs
    assert kwargs.get("response_type") == "ephemeral"
    assert kwargs.get("blocks")
    text_blob = "\n".join(
        b.get("text", {}).get("text", "")
        for b in kwargs["blocks"]
        if isinstance(b.get("text"), dict)
    )
    assert "new_query`: 1" in text_blob


def test_handler_swallows_read_recent_failure(mocker):
    mocker.patch(
        "src.handlers.research_bot_stats.is_admin", return_value=True
    )
    mocker.patch(
        "src.handlers.research_bot_stats.read_recent",
        side_effect=RuntimeError("disk gone"),
    )
    ack = MagicMock()
    respond = MagicMock()
    handle_stats_command(
        payload={"user_id": "U_ADMIN"}, ack=ack, respond=respond
    )

    ack.assert_called_once()
    respond.assert_called_once()
    text = respond.call_args.kwargs.get("text", "")
    assert "Could not read" in text
