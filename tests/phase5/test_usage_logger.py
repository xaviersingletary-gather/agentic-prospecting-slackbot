"""Spec §1.5 — Usage tracking JSONL logger."""
import json
from pathlib import Path

import pytest


@pytest.fixture
def tmp_log(tmp_path):
    return tmp_path / "usage.jsonl"


def test_log_usage_appends_one_jsonl_entry_per_call(tmp_log):
    from src.usage.logger import log_usage

    entry = {
        "slack_user_id": "U1",
        "slack_user_name": "rep_one",
        "account_queried": "Kroger",
        "personas_selected": ["operations_lead"],
        "apis_called": ["exa", "apollo"],
        "apollo_credits_used": 5,
        "exa_calls": 3,
        "contacts_returned": 8,
        "research_completed": True,
    }

    log_usage(entry, log_path=tmp_log)
    log_usage(entry, log_path=tmp_log)

    lines = tmp_log.read_text().strip().splitlines()
    assert len(lines) == 2
    parsed = json.loads(lines[0])
    assert parsed["account_queried"] == "Kroger"
    assert parsed["slack_user_id"] == "U1"


def test_log_usage_adds_timestamp_when_missing(tmp_log):
    from src.usage.logger import log_usage

    log_usage({"slack_user_id": "U1", "account_queried": "X"}, log_path=tmp_log)
    parsed = json.loads(tmp_log.read_text().strip())
    assert "timestamp" in parsed
    # ISO-8601 format check (date-time with T)
    assert "T" in parsed["timestamp"]


def test_read_recent_returns_entries_in_descending_timestamp_order(tmp_log):
    from src.usage.logger import log_usage, read_recent

    log_usage({"timestamp": "2026-04-30T10:00:00Z", "account_queried": "A"}, log_path=tmp_log)
    log_usage({"timestamp": "2026-04-30T12:00:00Z", "account_queried": "B"}, log_path=tmp_log)
    log_usage({"timestamp": "2026-04-30T11:00:00Z", "account_queried": "C"}, log_path=tmp_log)

    recent = read_recent(limit=10, log_path=tmp_log)
    assert [e["account_queried"] for e in recent] == ["B", "C", "A"]


def test_read_recent_caps_at_limit(tmp_log):
    from src.usage.logger import log_usage, read_recent

    for i in range(60):
        log_usage(
            {"timestamp": f"2026-04-30T10:{i:02d}:00Z", "account_queried": f"A{i}"},
            log_path=tmp_log,
        )

    recent = read_recent(limit=50, log_path=tmp_log)
    assert len(recent) == 50
    # Most recent first
    assert recent[0]["account_queried"] == "A59"


def test_read_recent_returns_empty_when_file_missing(tmp_log):
    from src.usage.logger import read_recent

    assert read_recent(limit=50, log_path=tmp_log) == []


def test_read_recent_skips_malformed_lines(tmp_log):
    from src.usage.logger import log_usage, read_recent

    log_usage({"timestamp": "2026-04-30T10:00:00Z", "account_queried": "A"}, log_path=tmp_log)
    with open(tmp_log, "a") as f:
        f.write("not json garbage\n")
    log_usage({"timestamp": "2026-04-30T11:00:00Z", "account_queried": "B"}, log_path=tmp_log)

    recent = read_recent(limit=10, log_path=tmp_log)
    accounts = [e["account_queried"] for e in recent]
    assert "A" in accounts and "B" in accounts
    assert len(recent) == 2
