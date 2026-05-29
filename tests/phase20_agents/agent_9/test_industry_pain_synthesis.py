"""Tests for Agent 9's Phase 9 LLM-synthesis "Why this fits" addition.

These cover the LLM pass that sits on top of the deterministic
classifier. The base classifier behavior is exercised in
`test_industry_pain.py` — these tests focus on the new INFERRED
"Why this fits" claim and its fallback path.

External LLM is mocked at the import path inside the agent module
(`src.research.agents.agent_9_industry_pain.synthesize_with_fallback`);
no real OpenRouter calls are made.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.research.agents import agent_9_industry_pain as a9
from src.research.agents.agent_9_industry_pain import (
    AgentContext,
    run,
)
from src.research.agents.contract import (
    AgentResult,
    Claim,
    SourceTag,
)


# ---------------------------------------------------------------------------
# Fixture helpers
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


def _3pl_priors():
    return [
        _wrap(
            "agent_4_customer_signals",
            [
                _public(
                    "Client SLA dashboards flag OTIF dips across two "
                    "fulfillment regions in Q1.",
                    url="https://example.com/3pl-report",
                ),
                _public(
                    "Third-party logistics network signs two new enterprise "
                    "retail clients with strict OTIF guarantees.",
                    url="https://example.com/3pl-news",
                ),
            ],
        ),
    ]


def _cpg_priors():
    """Kraft Heinz-style CPG priors."""
    return [
        _wrap(
            "agent_3_board_priorities",
            [
                _public(
                    "Consumer packaged goods leader cites S&OP and "
                    "demand signal accuracy as a 2026 board priority.",
                    url="https://example.com/cpg-10k",
                ),
                _inferred(
                    "Trade promotion lift on flagship SKUs creates "
                    "allocation pressure across DCs."
                ),
            ],
        ),
    ]


# ---------------------------------------------------------------------------
# 1. Success path — synthesis returns prose; 5 deterministic + 1 INFERRED.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_success_path_emits_5_deterministic_plus_why_this_fits():
    fake_prose = (
        "GEODIS lives or dies on OTIF and the client SLA dashboards "
        "they cite show exactly the kind of accuracy gap drones close. "
        "Two new enterprise retail clients raise the bar; missing "
        "another OTIF window costs a contract."
    )
    priors = _3pl_priors()

    with patch.object(
        a9, "synthesize_with_fallback", return_value=fake_prose
    ) as mock_synth:
        ctx = AgentContext(account_name="GEODIS", prior_results=priors)
        result = await run(ctx)

    # 5 deterministic claims still present.
    assert _by_text_prefix(result, "Industry:").text == "Industry: 3PL"
    assert _by_text_prefix(result, "Primary pain:").text == (
        "Primary pain: Customer Complaints + Inventory Accuracy"
    )
    assert _by_text_prefix(result, "Secondary pain:")
    assert _by_text_prefix(result, "Mirror language:")
    assert _by_text_prefix(result, "Recommended persona:").text == (
        "Recommended persona: FS"
    )

    # New "Why this fits" claim.
    why = _by_text_prefix(result, "Why this fits:")
    assert why.source_tag is SourceTag.INFERRED
    assert fake_prose in why.text
    # inference_logic must list at least one prior claim snippet, NOT a
    # full URL (per the brief).
    assert why.inference_logic
    assert "http" not in why.inference_logic
    assert "OTIF" in why.inference_logic or "SLA" in why.inference_logic

    # Synthesis was called exactly once.
    assert mock_synth.call_count == 1
    # Total claim count: 5 deterministic + 1 synthesized = 6.
    assert len(result.claims) == 6


# ---------------------------------------------------------------------------
# 2. Fallback path — synthesize returns the deterministic fallback string.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fallback_path_emits_why_this_fits_with_synthesis_fallback_marker():
    priors = _3pl_priors()
    # The helper short-circuits via the fallback arg passed by agent_9.
    # We simulate the failure mode by having the mock return whatever
    # `fallback=` it was called with.
    def _return_fallback(*, system_prompt, user_payload, fallback, **kwargs):
        return fallback

    with patch.object(
        a9, "synthesize_with_fallback", side_effect=_return_fallback
    ):
        ctx = AgentContext(account_name="GEODIS", prior_results=priors)
        result = await run(ctx)

    why = _by_text_prefix(result, "Why this fits:")
    assert why.source_tag is SourceTag.INFERRED
    # Fallback text content embeds the primary pain.
    assert "Customer Complaints + Inventory Accuracy" in why.text
    # inference_logic explicitly marks this as the fallback path.
    assert why.inference_logic
    assert "fallback" in why.inference_logic.lower() or (
        "synthesis failed" in why.inference_logic.lower()
    )


# ---------------------------------------------------------------------------
# 3. No-match path — LLM is NOT called when classification fails.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_match_path_does_not_call_llm():
    with patch.object(a9, "synthesize_with_fallback") as mock_synth:
        ctx = AgentContext(account_name="Mystery Co", prior_results=[])
        result = await run(ctx)

    # Single NOT_FOUND claim — existing behavior preserved.
    assert len(result.claims) == 1
    assert result.claims[0].source_tag is SourceTag.NOT_FOUND
    # LLM was not called.
    mock_synth.assert_not_called()


@pytest.mark.asyncio
async def test_no_match_path_software_only_priors_does_not_call_llm():
    priors = [
        _wrap(
            "agent_2_operating_baseline",
            [
                _inferred("SaaS company; product-led growth motion."),
                _inferred("Headquartered in San Francisco with remote staff."),
            ],
        ),
    ]
    with patch.object(a9, "synthesize_with_fallback") as mock_synth:
        ctx = AgentContext(account_name="Notion", prior_results=priors)
        result = await run(ctx)

    assert len(result.claims) == 1
    assert result.claims[0].source_tag is SourceTag.NOT_FOUND
    mock_synth.assert_not_called()


# ---------------------------------------------------------------------------
# 4. Prompt contains evidence quotes from top-matched prior claims.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_payload_contains_evidence_quotes():
    priors = _3pl_priors()
    captured = {}

    def _capture(*, system_prompt, user_payload, fallback, **kwargs):
        captured["system_prompt"] = system_prompt
        captured["user_payload"] = user_payload
        return "ok"

    with patch.object(a9, "synthesize_with_fallback", side_effect=_capture):
        ctx = AgentContext(account_name="GEODIS", prior_results=priors)
        await run(ctx)

    payload = captured["user_payload"]
    # Account name passed through.
    assert "GEODIS" in payload
    # Classified industry + primary/secondary pain passed through.
    assert "3PL" in payload
    assert "Customer Complaints + Inventory Accuracy" in payload
    assert "Multi-Site Consistency" in payload
    # At least one literal quote from the priors.
    assert "OTIF" in payload
    assert "Client SLA dashboards" in payload or "client SLA" in payload.lower()


# ---------------------------------------------------------------------------
# 5. System prompt embeds the grounding stanza (anti-AI-tells, em-dash rule).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_system_prompt_contains_grounding_rules():
    priors = _3pl_priors()
    captured = {}

    def _capture(*, system_prompt, user_payload, fallback, **kwargs):
        captured["system_prompt"] = system_prompt
        return "ok"

    with patch.object(a9, "synthesize_with_fallback", side_effect=_capture):
        ctx = AgentContext(account_name="GEODIS", prior_results=priors)
        await run(ctx)

    sp = captured["system_prompt"]
    # Anti-AI-tell vocabulary list — at least two of the forbidden words
    # must appear (synthesis.FORBIDDEN_AI_TELLS includes these).
    assert "leverage" in sp.lower()
    assert "seamless" in sp.lower() or "robust" in sp.lower()
    # Em-dash rule from build_grounding_stanza.
    assert "em-dash" in sp.lower() or "em-dashes" in sp.lower()
    # Role line per the brief.
    assert "Gather AI AE" in sp
    assert "3PL" in sp
    # Quote-evidence rule.
    assert "Quote at least one specific fact" in sp
    assert "4 sentences" in sp


# ---------------------------------------------------------------------------
# 6. CPG (Kraft Heinz-style) priors — captures sample output via mock.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cpg_priors_classify_and_synthesize():
    fake_prose = (
        "Kraft Heinz's stated focus on S&OP accuracy and trade promotion "
        "lift maps directly to the visibility gap CPG operators hit at DC "
        "scale. Allocation pressure on flagship SKUs is the trigger; "
        "drone counts make the demand signal trustworthy."
    )
    priors = _cpg_priors()

    with patch.object(
        a9, "synthesize_with_fallback", return_value=fake_prose
    ):
        ctx = AgentContext(account_name="Kraft Heinz", prior_results=priors)
        result = await run(ctx)

    assert _by_text_prefix(result, "Industry:").text == "Industry: CPG"
    why = _by_text_prefix(result, "Why this fits:")
    assert fake_prose in why.text
    assert why.source_tag is SourceTag.INFERRED
