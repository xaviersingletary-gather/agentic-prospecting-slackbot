"""Phase 21 — research-blob assembler tests.

Pure data transformation; no I/O.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.research.agents.aggregator import (
    assemble_research_blob,
    format_timestamp_for_slack,
)
from src.research.agents.contract import (
    AGENT_SECTIONS,
    AgentResult,
    Claim,
    SourceTag,
)


def _ar(name: str, *claims: Claim) -> AgentResult:
    return AgentResult(
        agent_name=name,
        section_title=AGENT_SECTIONS[name],
        claims=list(claims),
    )


def test_assemble_blob_includes_all_eight_sections_in_spec_order():
    # Pass only 2 results — assembler must synthesize placeholders for
    # the missing 6 slots so the renderer always sees 8 sections.
    results = [
        _ar(
            "agent_1_network_footprint",
            Claim(text="x", source_tag=SourceTag.PUBLIC, source_url="https://x"),
        ),
        _ar(
            "agent_3_board_priorities",
            Claim(text="y", source_tag=SourceTag.PUBLIC, source_url="https://y"),
        ),
    ]
    blob = assemble_research_blob(
        account_name="Walmart",
        intent="prospecting",
        agent_results=results,
    )

    assert blob["schema_version"] == 1
    assert blob["account_name"] == "Walmart"
    assert blob["intent"] == "prospecting"
    assert blob["generated_at"]
    names = [a["agent_name"] for a in blob["agents"]]
    assert names == list(AGENT_SECTIONS.keys())


def test_missing_agents_become_error_placeholders():
    blob = assemble_research_blob(
        account_name="Acme",
        intent="prospecting",
        agent_results=[],
    )
    for agent in blob["agents"]:
        assert len(agent["claims"]) == 1
        assert agent["claims"][0]["source_tag"] == "error"


def test_stats_counts_claims_by_tag_and_lists_errored_agents():
    results = [
        _ar(
            "agent_1_network_footprint",
            Claim(text="a", source_tag=SourceTag.PUBLIC, source_url="https://a"),
            Claim(text="b", source_tag=SourceTag.NOT_FOUND),
        ),
        _ar(
            "agent_5_shrink_compliance",
            Claim(text="EDGAR down", source_tag=SourceTag.ERROR),
        ),
    ]
    blob = assemble_research_blob(
        account_name="X", intent=None, agent_results=results
    )
    stats = blob["stats"]
    assert stats["total_claims"] == 3
    assert stats["claims_by_tag"]["public"] == 1
    assert stats["claims_by_tag"]["not_found"] == 1
    assert stats["claims_by_tag"]["error"] == 1
    assert "agent_5_shrink_compliance" in stats["errored_agents"]


def test_explicit_timestamp_is_respected():
    fixed = datetime(2026, 5, 26, 18, 14, tzinfo=timezone.utc)
    blob = assemble_research_blob(
        account_name="X",
        intent=None,
        agent_results=[],
        timestamp=fixed,
    )
    assert blob["generated_at"] == fixed.isoformat()


def test_format_timestamp_for_slack_renders_human_string():
    fixed = datetime(2026, 5, 26, 18, 14, tzinfo=timezone.utc)
    out = format_timestamp_for_slack(fixed.isoformat())
    assert out.startswith("Research pulled ")
    assert "2026" in out
    assert "ET" in out


def test_format_timestamp_for_slack_falls_back_on_bad_input():
    out = format_timestamp_for_slack("not a real timestamp")
    assert "not a real timestamp" in out
