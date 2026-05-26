"""Phase 23 — typed JSONL event helpers (May 26 V1 spec).

Each helper persists exactly the spec-required fields and never raises
on disk-write failure.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.usage import v1_events
from src.usage.logger import read_recent


@pytest.fixture
def tmp_log(tmp_path, monkeypatch):
    """Point the usage logger at a temp JSONL file for the test."""
    p = tmp_path / "usage.jsonl"
    monkeypatch.setenv("USAGE_LOG_PATH", str(p))
    return p


def _last_entry(tmp_log: Path) -> dict:
    assert tmp_log.exists()
    lines = tmp_log.read_text().strip().splitlines()
    assert lines, "JSONL is empty"
    return json.loads(lines[-1])


def test_log_new_query_persists_required_fields(tmp_log):
    v1_events.log_new_query(
        account_name="Walmart",
        rep_id="U_REP",
        intent="prospecting",
        thread_ts="T_THREAD",
        total_ms=2300,
        agent_durations_ms={"agent_1_network_footprint": 800},
        errored_agents=["agent_5_shrink_compliance"],
        total_claims=23,
    )
    e = _last_entry(tmp_log)
    assert e["event_type"] == "new_query"
    assert e["account_name"] == "Walmart"
    assert e["rep_id"] == "U_REP"
    assert e["intent"] == "prospecting"
    assert e["thread_ts"] == "T_THREAD"
    assert e["total_ms"] == 2300
    assert e["agent_durations_ms"]["agent_1_network_footprint"] == 800
    assert e["errored_agents"] == ["agent_5_shrink_compliance"]
    assert e["total_claims"] == 23
    assert e["timestamp"]


def test_log_followup_persists_lengths_only_never_question_text(tmp_log):
    v1_events.log_followup(
        thread_ts="T",
        rep_id="U",
        question_length=42,
        answer_length=180,
        latency_ms=950,
        used_v2_path=True,
    )
    e = _last_entry(tmp_log)
    assert e["event_type"] == "followup"
    assert e["question_length"] == 42
    assert e["answer_length"] == 180
    assert e["latency_ms"] == 950
    assert e["used_v2_path"] is True
    # Defensive: no raw question text stored under any key.
    for forbidden in ("question", "answer", "raw_query", "user_input"):
        assert forbidden not in e


def test_log_agent_failure_persists_error_type_name_only(tmp_log):
    v1_events.log_agent_failure(
        agent_name="agent_5_shrink_compliance",
        account_name="GEODIS",
        error_type="EDGAR fetch timed out after 8s",
        recovery="section rendered as ERROR; other agents continued",
    )
    e = _last_entry(tmp_log)
    assert e["event_type"] == "agent_failure"
    assert e["agent_name"] == "agent_5_shrink_compliance"
    assert e["account_name"] == "GEODIS"
    assert e["error_type"].startswith("EDGAR fetch")
    assert "ERROR" in e["recovery"]


def test_log_disambig_captures_term_candidates_and_choice(tmp_log):
    v1_events.log_disambig(
        account_name="Volvo",
        rep_id="U",
        candidates=["Volvo Group", "Volvo Cars"],
        chosen="Volvo Group",
    )
    e = _last_entry(tmp_log)
    assert e["event_type"] == "disambig"
    assert e["account_name"] == "Volvo"
    assert e["candidates"] == ["Volvo Group", "Volvo Cars"]
    assert e["chosen"] == "Volvo Group"


def test_safe_emit_swallows_disk_errors(monkeypatch):
    """A broken USAGE_LOG_PATH must not raise — observability never blocks
    the request path."""
    monkeypatch.setenv("USAGE_LOG_PATH", "/dev/null/does/not/exist.jsonl")
    # Must not raise.
    v1_events.log_followup(
        thread_ts="T",
        rep_id="U",
        question_length=0,
        answer_length=0,
        latency_ms=0,
        used_v2_path=False,
    )


def test_read_recent_returns_all_entries_with_descending_or_equal_timestamps(
    tmp_log,
):
    for i, name in enumerate(["A", "B", "C"]):
        v1_events.log_new_query(
            account_name=name,
            rep_id="U",
            intent=None,
            thread_ts=f"T{i}",
            total_ms=i * 1000,
            agent_durations_ms={},
            errored_agents=[],
            total_claims=i,
        )
    entries = read_recent(limit=10, log_path=tmp_log)
    # All three present; ordering within a single-second timestamp window
    # is unspecified — the logger's sort key has 1s resolution.
    assert {e["account_name"] for e in entries} == {"A", "B", "C"}
    ts = [e["timestamp"] for e in entries]
    assert ts == sorted(ts, reverse=True)
