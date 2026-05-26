"""Phase 20 — AgentResult / Claim contract tests.

Pure-Python; no I/O. These pin the shape every agent must satisfy.
"""
from __future__ import annotations

import pytest

from src.research.agents.contract import (
    AGENT_SECTIONS,
    AgentResult,
    Claim,
    SourceTag,
)


# ---------------------------------------------------------------------------
# Claim validation
# ---------------------------------------------------------------------------


def test_public_claim_requires_source_url():
    Claim(text="Operates 47 DCs", source_tag=SourceTag.PUBLIC, source_url="https://x")
    with pytest.raises(ValueError):
        Claim(text="x", source_tag=SourceTag.PUBLIC)


def test_inferred_claim_requires_inference_logic():
    Claim(
        text="~35 DCs",
        source_tag=SourceTag.INFERRED,
        inference_logic="revenue / industry pallets-per-dc baseline",
    )
    with pytest.raises(ValueError):
        Claim(text="x", source_tag=SourceTag.INFERRED)


def test_internal_claim_requires_source():
    Claim(
        text="Open opp, $400K",
        source_tag=SourceTag.INTERNAL,
        source="HubSpot deal 12345",
    )
    with pytest.raises(ValueError):
        Claim(text="x", source_tag=SourceTag.INTERNAL)


def test_not_found_and_error_claims_only_need_text():
    Claim(text="No DC count disclosed", source_tag=SourceTag.NOT_FOUND)
    Claim(text="Exa timed out", source_tag=SourceTag.ERROR)


def test_claim_text_must_be_non_empty():
    with pytest.raises(ValueError):
        Claim(text="", source_tag=SourceTag.NOT_FOUND)


def test_source_tag_accepts_string_or_enum():
    c1 = Claim(text="x", source_tag="not_found")
    c2 = Claim(text="x", source_tag=SourceTag.NOT_FOUND)
    assert c1.source_tag is SourceTag.NOT_FOUND
    assert c2.source_tag is SourceTag.NOT_FOUND


def test_claim_to_dict_drops_none_and_serializes_enum():
    c = Claim(text="hi", source_tag=SourceTag.PUBLIC, source_url="https://x")
    d = c.to_dict()
    assert d == {"text": "hi", "source_tag": "public", "source_url": "https://x"}


# ---------------------------------------------------------------------------
# AgentResult
# ---------------------------------------------------------------------------


def test_agent_result_round_trips_through_to_dict():
    r = AgentResult(
        agent_name="agent_1_network_footprint",
        section_title="Network Footprint",
        claims=[
            Claim(text="47 DCs", source_tag=SourceTag.PUBLIC, source_url="https://x"),
            Claim(text="No sqft disclosed", source_tag=SourceTag.NOT_FOUND),
        ],
        duration_ms=1234,
    )
    d = r.to_dict()
    assert d["agent_name"] == "agent_1_network_footprint"
    assert len(d["claims"]) == 2
    assert d["claims"][0]["source_tag"] == "public"
    assert d["claims"][1]["source_tag"] == "not_found"
    assert d["duration_ms"] == 1234


def test_agent_result_error_helper_builds_single_error_claim():
    r = AgentResult.error(
        agent_name="agent_5_shrink_compliance",
        section_title="Shrink & Compliance",
        reason="EDGAR fetch timed out after 8s",
    )
    assert len(r.claims) == 1
    assert r.claims[0].source_tag is SourceTag.ERROR
    assert "EDGAR" in r.claims[0].text


# ---------------------------------------------------------------------------
# Registry shape
# ---------------------------------------------------------------------------


def test_agent_sections_has_all_eight_in_spec_order():
    keys = list(AGENT_SECTIONS.keys())
    assert keys == [
        "agent_1_network_footprint",
        "agent_2_operating_baseline",
        "agent_3_board_priorities",
        "agent_4_customer_signals",
        "agent_5_shrink_compliance",
        "agent_6_automation_stack",
        "agent_7_department_angles",
        "agent_8_contacts",
    ]
