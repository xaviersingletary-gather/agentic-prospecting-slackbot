"""Tests for Agent 9 — Industry & Pain Matcher.

The agent is pure deterministic synthesis: feed it `prior_results` shaped
like real `AgentResult`s, assert the classification + persona pick + the
verbatim pitch-tone rule. No external mocks needed.
"""
from __future__ import annotations

import pytest

from src.research.agents.agent_9_industry_pain import (
    AgentContext,
    PERSONA_PITCH_RULES,
    run,
)
from src.research.agents.contract import (
    AgentResult,
    Claim,
    SourceTag,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _public(text: str, url: str = "https://example.com/source") -> Claim:
    return Claim(text=text, source_tag=SourceTag.PUBLIC, source_url=url)


def _inferred(text: str) -> Claim:
    return Claim(
        text=text,
        source_tag=SourceTag.INFERRED,
        inference_logic="test fixture",
    )


def _wrap(name: str, claims) -> AgentResult:
    return AgentResult(
        agent_name=name,
        section_title=name.replace("_", " ").title(),
        claims=list(claims),
    )


def _by_text_prefix(result: AgentResult, prefix: str) -> Claim:
    for c in result.claims:
        if c.text.startswith(prefix):
            return c
    raise AssertionError(
        f"No claim starting with {prefix!r}; got {[c.text for c in result.claims]}"
    )


# ---------------------------------------------------------------------------
# Industry classification — Pharma
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pharma_classification_emits_full_mapping():
    priors = [
        _wrap(
            "agent_5_shrink_compliance",
            [
                _public(
                    "10-K Risk Factors note FDA audit findings and lot "
                    "traceability gaps across distribution sites.",
                    url="https://www.sec.gov/Archives/example-10k.htm",
                ),
                _public(
                    "Controlled substance recall protocols expanded in "
                    "2026 per company press release.",
                    url="https://example.com/pharma-press",
                ),
            ],
        ),
    ]

    ctx = AgentContext(account_name="ExamplePharma", prior_results=priors)
    result = await run(ctx)

    assert result.agent_name == "agent_9_industry_pain"
    industry_claim = _by_text_prefix(result, "Industry:")
    assert industry_claim.text == "Industry: Pharmaceutical"
    # Multiple PUBLIC priors carried the trigger, so PUBLIC with URL.
    assert industry_claim.source_tag is SourceTag.PUBLIC
    assert industry_claim.source_url

    primary = _by_text_prefix(result, "Primary pain:")
    assert primary.text == "Primary pain: Data Privacy + Inventory Accuracy"
    assert primary.source_tag is SourceTag.INFERRED

    mirror = _by_text_prefix(result, "Mirror language:")
    assert "USP 800" in mirror.text
    assert "FDA audit" in mirror.text

    persona = _by_text_prefix(result, "Recommended persona:")
    assert persona.text == "Recommended persona: Safety"
    assert persona.source_tag is SourceTag.INFERRED


# ---------------------------------------------------------------------------
# Industry classification — 3PL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_3pl_classification_recommends_financial_sponsor():
    priors = [
        _wrap(
            "agent_4_customer_signals",
            [
                _public(
                    "Client SLA dashboards flag OTIF dips across two "
                    "fulfillment regions.",
                    url="https://example.com/3pl-report",
                ),
                _inferred(
                    "Third-party logistics network of 40+ sites inferred "
                    "from job posting density."
                ),
            ],
        ),
    ]

    ctx = AgentContext(account_name="ExampleLogistics", prior_results=priors)
    result = await run(ctx)

    industry = _by_text_prefix(result, "Industry:")
    assert industry.text == "Industry: 3PL"

    persona = _by_text_prefix(result, "Recommended persona:")
    assert persona.text == "Recommended persona: FS"


# ---------------------------------------------------------------------------
# No-match — empty priors and software-only keywords
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_priors_emits_not_found():
    ctx = AgentContext(account_name="Mystery Co", prior_results=[])
    result = await run(ctx)

    assert len(result.claims) == 1
    only = result.claims[0]
    assert only.source_tag is SourceTag.NOT_FOUND
    assert "could not" in only.text.lower() or "gap" in only.text.lower()


@pytest.mark.asyncio
async def test_software_only_priors_emits_not_found():
    # Software-vendor-flavored priors with zero industry keywords.
    priors = [
        _wrap(
            "agent_2_operating_baseline",
            [
                _inferred("SaaS company; product-led growth motion."),
                _inferred("Headquartered in San Francisco with remote staff."),
            ],
        ),
    ]
    ctx = AgentContext(account_name="Notion", prior_results=priors)
    result = await run(ctx)

    assert len(result.claims) == 1
    assert result.claims[0].source_tag is SourceTag.NOT_FOUND


# ---------------------------------------------------------------------------
# Tie-break — heavy grocery beats lighter retail-distribution overlap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_grocery_beats_retail_when_grocery_signal_dominates():
    priors = [
        _wrap(
            "agent_2_operating_baseline",
            [
                _inferred(
                    "Operates a network of supermarket distribution "
                    "centers handling perishable inventory; FIFO rotation "
                    "and cold chain integrity are central. Some retail "
                    "store replenishment also occurs."
                ),
                _inferred(
                    "Shelf life pressures on dairy and produce drive "
                    "shrink targets; perishable rotation is the dominant "
                    "operational concern."
                ),
            ],
        ),
    ]
    ctx = AgentContext(account_name="ExampleGrocer", prior_results=priors)
    result = await run(ctx)

    industry = _by_text_prefix(result, "Industry:")
    assert industry.text == "Industry: Grocery"


# ---------------------------------------------------------------------------
# Pitch-tone rule is embedded verbatim in the persona claim
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pitch_tone_rule_present_in_inference_logic():
    priors = [
        _wrap(
            "agent_5_shrink_compliance",
            [
                _public(
                    "FDA audit findings flag controlled substance lot "
                    "tracking gaps.",
                    url="https://example.com/pharma",
                ),
            ],
        ),
    ]
    ctx = AgentContext(account_name="ExamplePharma", prior_results=priors)
    result = await run(ctx)

    persona = _by_text_prefix(result, "Recommended persona:")
    # The Safety pitch-tone rule is encoded verbatim in PERSONA_PITCH_RULES.
    assert persona.inference_logic is not None
    safety_rule = PERSONA_PITCH_RULES["Safety"]
    assert "Lead with safety and certification" in persona.inference_logic
    assert safety_rule in persona.inference_logic


# ---------------------------------------------------------------------------
# Schema round-trip — every emitted claim must serialize cleanly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_claims_round_trip_to_dict():
    priors = [
        _wrap(
            "agent_4_customer_signals",
            [
                _public(
                    "Client SLA reviews highlight OTIF misses on the "
                    "third-party logistics network.",
                    url="https://example.com/3pl",
                ),
            ],
        ),
    ]
    ctx = AgentContext(account_name="ExampleLogistics", prior_results=priors)
    result = await run(ctx)

    serialized = result.to_dict()
    assert serialized["agent_name"] == "agent_9_industry_pain"
    assert serialized["section_title"] == "Industry & Pain Matcher"
    assert isinstance(serialized["claims"], list)
    assert len(serialized["claims"]) == 5
    for blob in serialized["claims"]:
        assert "text" in blob
        assert "source_tag" in blob
        assert blob["source_tag"] in (
            "public",
            "inferred",
            "not_found",
        )


# ---------------------------------------------------------------------------
# Industry claim downgrade — no PUBLIC prior carries trigger → INFERRED
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_industry_claim_downgrades_to_inferred_when_no_public_url():
    # Trigger keywords only appear in INFERRED priors (no source_url).
    priors = [
        _wrap(
            "agent_2_operating_baseline",
            [
                _inferred(
                    "Operates a third-party logistics fulfillment "
                    "network with client SLA reporting."
                ),
            ],
        ),
    ]
    ctx = AgentContext(account_name="ExampleLogistics", prior_results=priors)
    result = await run(ctx)

    industry = _by_text_prefix(result, "Industry:")
    assert industry.source_tag is SourceTag.INFERRED
    assert industry.source_url is None
    assert industry.inference_logic is not None
