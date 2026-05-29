"""Tests for Agent 7's LLM-synthesis layer (Phase 9 — May 29 V1.1).

Agent 7's deterministic keyword-matching scaffolding stays in place as
the hallucination guard. On top of it, an LLM call turns the matched
anchor claim into a 2-sentence persona-aware angle plus a discovery
question. These tests cover:

  1. Success path: when `synthesize_with_fallback` returns prose, the
     persona Claim's text matches the mocked return and the
     `inference_logic` still cites the matched prior claim.
  2. Fallback path: when `synthesize_with_fallback` returns the agent's
     fallback sentinel (LLM unavailable / failed), the persona Claim
     emits via the deterministic template — never silently dropped.
  3. No signal: a persona with no matched anchor still emits a
     NOT_FOUND claim (existing behavior preserved alongside synthesis).
  4. Prompt contains pitch-tone: the system prompt passed to the
     synthesis helper contains the persona's pitch-tone rule for at
     least 2 of the 5 personas.
  5. Prompt grounding: the system prompt includes the anti-AI-tell
     vocabulary (e.g. "leverage", "robust") and the em-dashes rule.
"""
from __future__ import annotations

import asyncio
from typing import List
from unittest.mock import patch

import pytest

from src.research.agents import agent_7_department_angles as agent_7
from src.research.agents.agent_7_department_angles import (
    AGENT_NAME,
    PERSONA_ORDER,
    SECTION_TITLE,
    _SYNTH_FALLBACK_SENTINEL,
    AgentContext,
    run,
)
from src.research.agents.contract import AgentResult, Claim, SourceTag


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _public(text: str, url: str = "https://example.com/article") -> Claim:
    return Claim(text=text, source_tag=SourceTag.PUBLIC, source_url=url)


def _wrap(agent_name: str, section_title: str, claims: List[Claim]) -> AgentResult:
    return AgentResult(
        agent_name=agent_name,
        section_title=section_title,
        claims=claims,
    )


def _run(ctx: AgentContext) -> AgentResult:
    return asyncio.run(run(ctx))


def _rich_priors() -> List[AgentResult]:
    """Priors that produce at least one matched signal per persona."""
    return [
        _wrap(
            "agent_5_shrink_compliance",
            "Shrink & Compliance",
            [
                _public(
                    "Walmart reported $3B in inventory shrink for the fiscal year, "
                    "calling out write-offs as a margin pressure in earnings.",
                    url="https://example.com/walmart-shrink",
                ),
            ],
        ),
        _wrap(
            "agent_6_automation_stack",
            "Automation Stack",
            [
                _public(
                    "Walmart expanded its Symbotic robotics and warehouse "
                    "automation deployment to 25 regional DCs with WMS integration.",
                    url="https://example.com/walmart-automation",
                ),
            ],
        ),
        _wrap(
            "agent_1_network_footprint",
            "Network Footprint",
            [
                _public(
                    "Walmart announced four new DC openings and a multi-year "
                    "facility expansion plan to grow fulfillment throughput.",
                    url="https://example.com/walmart-dc",
                ),
            ],
        ),
        _wrap(
            "agent_3_board_priorities",
            "Board Priorities",
            [
                _public(
                    "Walmart's board flagged OSHA safety and compliance audits "
                    "as a top operational priority after a recent incident.",
                    url="https://example.com/walmart-board",
                ),
            ],
        ),
    ]


# ---------------------------------------------------------------------------
# 1. Success path — synthesized prose lands in the Claim
# ---------------------------------------------------------------------------


def test_synthesis_success_replaces_template_text():
    """When the LLM returns prose, the Claim text carries it (with the
    persona-key prefix preserved) and inference_logic still cites the
    upstream snippet."""
    fake_prose = (
        "Your shrink number is the wedge, not the technology. "
        "What is the labor cost per cycle count today?"
    )

    with patch.object(
        agent_7, "synthesize_with_fallback", return_value=fake_prose
    ) as mock_synth:
        ctx = AgentContext(account_name="Walmart", prior_results=_rich_priors())
        result = _run(ctx)

    # All 5 personas matched on the rich-priors fixture, so we expect
    # 5 INFERRED claims and 5 synthesis calls.
    inferred = [c for c in result.claims if c.source_tag is SourceTag.INFERRED]
    assert len(inferred) == 5
    assert mock_synth.call_count == 5

    for claim in inferred:
        # Synthesized prose ended up in the text.
        assert fake_prose in claim.text, (
            f"Expected synthesized prose in claim text, got: {claim.text!r}"
        )
        # Persona-key prefix is still present so downstream parsing
        # (renderer, tests, formatters) can still locate the persona.
        persona_present = any(p in claim.text for p in PERSONA_ORDER)
        assert persona_present, f"No persona key in: {claim.text!r}"
        # inference_logic still cites the upstream snippet — the
        # deterministic scaffolding is the hallucination guard.
        assert claim.inference_logic
        assert "Synthesized from" in claim.inference_logic
        assert "Source snippet" in claim.inference_logic


# ---------------------------------------------------------------------------
# 2. Fallback path — synth returns sentinel → deterministic template emits
# ---------------------------------------------------------------------------


def test_synthesis_fallback_emits_deterministic_template():
    """When the LLM call fails (helper returns the fallback string), the
    persona Claim must still be emitted via the deterministic template
    — no silent drop."""
    # synthesize_with_fallback echoes back whatever `fallback=` we
    # passed in. The agent uses _SYNTH_FALLBACK_SENTINEL as its
    # fallback, so on failure the helper returns that sentinel and
    # we expect the deterministic template instead.
    with patch.object(
        agent_7, "synthesize_with_fallback", return_value=_SYNTH_FALLBACK_SENTINEL
    ) as mock_synth:
        ctx = AgentContext(account_name="Walmart", prior_results=_rich_priors())
        result = _run(ctx)

    inferred = [c for c in result.claims if c.source_tag is SourceTag.INFERRED]
    assert len(inferred) == 5, "Persona output must not be silently dropped"
    assert mock_synth.call_count == 5

    for claim in inferred:
        # Deterministic template fingerprints: "angle:" + Anchor: "..."
        assert "angle:" in claim.text.lower(), (
            f"Expected deterministic template, got: {claim.text!r}"
        )
        assert "Anchor:" in claim.text, (
            f"Expected anchor line in deterministic template: {claim.text!r}"
        )
        assert claim.inference_logic and "Synthesized from" in claim.inference_logic


def test_synthesis_empty_string_falls_back_to_template():
    """An empty / whitespace string from the helper should also trigger
    the deterministic fallback path."""
    with patch.object(agent_7, "synthesize_with_fallback", return_value="   "):
        ctx = AgentContext(account_name="Walmart", prior_results=_rich_priors())
        result = _run(ctx)

    inferred = [c for c in result.claims if c.source_tag is SourceTag.INFERRED]
    assert len(inferred) == 5
    for claim in inferred:
        assert "Anchor:" in claim.text


# ---------------------------------------------------------------------------
# 3. No signal — NOT_FOUND still emitted for unmatched personas
# ---------------------------------------------------------------------------


def test_persona_without_anchor_still_emits_not_found():
    """Existing behavior must hold: a persona with no matched anchor
    gets a NOT_FOUND claim, and no synthesis call is made for it."""
    # Single prior touches FS (shrink) and tangentially ODM (inventory).
    # TDM / IT / Safety have no anchor.
    priors = [
        _wrap(
            "agent_5_shrink_compliance",
            "Shrink & Compliance",
            [
                _public(
                    "GEODIS disclosed an internal cost reduction initiative "
                    "tied to reducing inventory shrink across its US 3PL network.",
                    url="https://example.com/geodis-shrink",
                ),
            ],
        ),
    ]

    fake_prose = "Synthesized angle. Discovery question?"
    with patch.object(
        agent_7, "synthesize_with_fallback", return_value=fake_prose
    ) as mock_synth:
        ctx = AgentContext(account_name="GEODIS", prior_results=priors)
        result = _run(ctx)

    assert result.agent_name == AGENT_NAME
    assert result.section_title == SECTION_TITLE
    assert len(result.claims) == 5

    not_found = [c for c in result.claims if c.source_tag is SourceTag.NOT_FOUND]
    inferred = [c for c in result.claims if c.source_tag is SourceTag.INFERRED]

    # At least one persona has no signal and must be NOT_FOUND.
    assert len(not_found) >= 2, (
        f"Expected NOT_FOUND for unmatched personas; got {len(not_found)}"
    )

    # NOT_FOUND personas must not have triggered a synthesis call.
    # Synthesis is called only when there's an anchor to ground in.
    assert mock_synth.call_count == len(inferred), (
        f"Synthesis called {mock_synth.call_count} times, expected "
        f"{len(inferred)} (one per INFERRED persona)"
    )

    # NOT_FOUND claims should describe the gap so the rep can ask in
    # discovery (existing behavior).
    for claim in not_found:
        assert claim.text
        assert "no upstream signal" in claim.text.lower() or \
            "flag in discovery" in claim.text.lower()


# ---------------------------------------------------------------------------
# 4. Prompt contains pitch-tone — for at least 2 of the 5 personas
# ---------------------------------------------------------------------------


def test_system_prompt_contains_pitch_tone_per_persona():
    """The per-persona system prompt must embed that persona's pitch-tone
    rule verbatim. We capture every system prompt sent and assert at
    least 2 of the 5 personas had their pitch-tone fragment in-prompt."""
    captured_prompts: List[str] = []

    def _capture(*, system_prompt, user_payload, fallback, **kwargs):
        captured_prompts.append(system_prompt)
        return "stub angle. stub question?"

    with patch.object(agent_7, "synthesize_with_fallback", side_effect=_capture):
        ctx = AgentContext(account_name="Walmart", prior_results=_rich_priors())
        _run(ctx)

    # Pitch-tone fingerprints — distinct keywords from each persona's
    # tone rule that would not show up by accident.
    pitch_tone_fingerprints = {
        "TDM": "15-min config",
        "ODM": "cycle counts",
        "FS": "payback period",
        "IT": "ISO 27001",
        "Safety": "pallets and labels",
    }

    matches: List[str] = []
    for persona_key, fingerprint in pitch_tone_fingerprints.items():
        for prompt in captured_prompts:
            if fingerprint in prompt and persona_key in prompt:
                matches.append(persona_key)
                break

    assert len(set(matches)) >= 2, (
        f"Expected pitch-tone embedded for at least 2 personas; "
        f"matched {sorted(set(matches))}"
    )


def test_each_persona_prompt_carries_its_own_tone():
    """Stronger version: each persona's prompt carries that persona's
    pitch-tone fingerprint exclusively. Defends against a copy-paste
    error that would broadcast one tone to all personas."""
    # Map persona_key → captured prompt for that persona.
    per_persona_prompt = {}

    def _capture(*, system_prompt, user_payload, fallback, **kwargs):
        for persona_key in PERSONA_ORDER:
            # The role line embeds the persona key — use it to bucket.
            if f"({persona_key})" in system_prompt:
                per_persona_prompt[persona_key] = system_prompt
                break
        return "stub angle. stub question?"

    with patch.object(agent_7, "synthesize_with_fallback", side_effect=_capture):
        ctx = AgentContext(account_name="Walmart", prior_results=_rich_priors())
        _run(ctx)

    pitch_tone_fingerprints = {
        "TDM": "15-min config",
        "ODM": "cycle counts",
        "FS": "payback period",
        "IT": "ISO 27001",
        "Safety": "pallets and labels",
    }
    for persona_key, fingerprint in pitch_tone_fingerprints.items():
        assert persona_key in per_persona_prompt, (
            f"No prompt captured for {persona_key}"
        )
        prompt = per_persona_prompt[persona_key]
        assert fingerprint in prompt, (
            f"{persona_key} prompt missing fingerprint {fingerprint!r}"
        )


# ---------------------------------------------------------------------------
# 5. Prompt grounding — anti-AI-tell + em-dashes
# ---------------------------------------------------------------------------


def test_system_prompt_includes_grounding_stanza():
    """The system prompt must include the project-wide anti-AI-tell
    vocabulary (sample: "leverage", "robust") and the em-dashes rule."""
    captured_prompts: List[str] = []

    def _capture(*, system_prompt, user_payload, fallback, **kwargs):
        captured_prompts.append(system_prompt)
        return "stub angle. stub question?"

    with patch.object(agent_7, "synthesize_with_fallback", side_effect=_capture):
        ctx = AgentContext(account_name="Walmart", prior_results=_rich_priors())
        _run(ctx)

    assert captured_prompts, "Expected at least one synthesis call"
    for prompt in captured_prompts:
        # Anti-AI-tell vocabulary fingerprints.
        assert "leverage" in prompt, "Anti-AI-tell list missing 'leverage'"
        assert "robust" in prompt, "Anti-AI-tell list missing 'robust'"
        # Em-dashes rule.
        assert "em-dashes" in prompt, "Grounding stanza missing em-dashes rule"


def test_user_payload_contains_account_intent_and_anchor():
    """The user payload should carry the account name, intent, and the
    matched prior claim text the LLM is grounding in."""
    captured_payloads: List[str] = []

    def _capture(*, system_prompt, user_payload, fallback, **kwargs):
        captured_payloads.append(user_payload)
        return "stub angle. stub question?"

    with patch.object(agent_7, "synthesize_with_fallback", side_effect=_capture):
        ctx = AgentContext(
            account_name="Walmart",
            intent="qualify_account",
            prior_results=_rich_priors(),
        )
        _run(ctx)

    assert captured_payloads
    joined = "\n---\n".join(captured_payloads)
    assert "Walmart" in joined
    assert "qualify_account" in joined
    # At least one payload should quote an anchor from the rich priors.
    assert "shrink" in joined.lower() or "automation" in joined.lower()
