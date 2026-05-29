"""Tests for Agent 10 — Hook Candidates + Why Now.

Agent 10 has one external dependency now: a small LLM synthesis pass
that turns the deterministic-picker output into actual first-line
cold-outreach copy. Tests mock `synthesize_with_fallback` at the
module's import site so no real OpenRouter call ever fires.

Coverage:

  1. Rich-priors (Walmart-style): shrink event + automation deployment,
     both dated and sourced. Mocked LLM returns prose openers — expect
     2–3 PUBLIC hooks carrying the mocked text + underlying source_url +
     date, plus one INFERRED Why Now carrying the mocked Why-Now text.
  2. Sparse-priors (GEODIS-style): a single dated PUBLIC claim. Expect
     1 PUBLIC hook + 2 NOT_FOUND "HOOK TBD" pads + an INFERRED Why Now.
  3. No-dated-priors (Notion-style): claims present but with no `date`
     field. Expect 3 NOT_FOUND HOOK TBD claims + a NOT_FOUND Why Now,
     AND `synthesize_with_fallback` is NEVER called (no LLM spend on a
     deterministic answer).
  4. Stale priors (>12 months) filtered out before any LLM call.
  5. Hook fallback path: when synthesis returns the fallback (LLM key
     missing / timeout / etc.), hooks STILL emit using the deterministic
     "Hook: {claim text} (anchor: {section})" template — no silent drop.
  6. Why Now fallback path: when synthesis returns the fallback, Why Now
     emits using the deterministic 2-sentence template.
  7. Why Now picks highest-priority section over freshness.
  8. Hook system prompt forbids generic openers (literal SDR rule).
  9. Why Now system prompt forbids generic openers (literal SDR rule).
 10. Schema round-trip: every claim survives `.to_dict()` cleanly.
 11. industry_pain_result is ignored as a hook source.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from unittest.mock import patch

import pytest

from src.research.agents import agent_10_hook_candidates as agent_mod
from src.research.agents.agent_10_hook_candidates import (
    AGENT_NAME,
    GENERIC_OPENER_BAN,
    HOOK_TBD_TEXT,
    MAX_HOOKS,
    SECTION_TITLE,
    AgentContext,
    _hook_system_prompt,
    _why_now_system_prompt,
    run,
)
from src.research.agents.contract import AgentResult, Claim, SourceTag


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _public(
    text: str,
    url: str = "https://example.com/article",
    date: Optional[str] = None,
) -> Claim:
    return Claim(
        text=text,
        source_tag=SourceTag.PUBLIC,
        source_url=url,
        date=date,
    )


def _wrap(
    agent_name: str,
    section_title: str,
    claims: List[Claim],
) -> AgentResult:
    return AgentResult(
        agent_name=agent_name,
        section_title=section_title,
        claims=claims,
    )


def _months_ago(n: int) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=30 * n)
    return dt.strftime("%Y-%m-%d")


def _run(ctx: AgentContext) -> AgentResult:
    return asyncio.run(run(ctx))


# Each agent-module call to `synthesize_with_fallback` passes a
# `log_label`. Hook calls use "[agent_10][hook]"; Why Now uses
# "[agent_10][why_now]". The test stub routes on that label so a single
# fixture can return distinct prose for hooks vs. Why Now.
def _routed_synthesis(hook_text: str, why_now_text: str):
    def _fn(*, system_prompt, user_payload, fallback, log_label="", **_kw):
        if "why_now" in log_label:
            return why_now_text
        return hook_text
    return _fn


def _fallback_synthesis():
    """Return the deterministic fallback unchanged — simulates a no-LLM
    environment (no key set, timeout, etc.)."""
    def _fn(*, system_prompt, user_payload, fallback, log_label="", **_kw):
        return fallback
    return _fn


# ---------------------------------------------------------------------------
# 1. Rich priors — Walmart-style — LLM-synthesized hooks + Why Now
# ---------------------------------------------------------------------------


def test_rich_priors_walmart_emits_synthesized_public_hooks_and_inferred_why_now():
    """Two dated triggers → 2 PUBLIC hooks (mocked LLM text) + INFERRED Why Now."""
    shrink_url = "https://walmart.example/q3-2025-earnings"
    automation_url = "https://walmart.example/symbotic-25-dcs"

    priors = [
        _wrap(
            "agent_5_shrink_compliance",
            "Shrink & Compliance",
            [
                _public(
                    "Q3 2025 earnings call: CFO flagged $3B shrink write-offs "
                    "as a top containment priority.",
                    url=shrink_url,
                    date=_months_ago(2),
                ),
            ],
        ),
        _wrap(
            "agent_6_automation_stack",
            "Automation Stack",
            [
                _public(
                    "Symbotic rollout extended to 25 distribution centers "
                    "announced in Q4 2025.",
                    url=automation_url,
                    date=_months_ago(4),
                ),
            ],
        ),
    ]

    hook_prose = (
        "Saw your CFO flag $3B in shrink write-offs on the Q3 2025 call "
        "as a containment priority — wanted to share how we keep that "
        "number from coming back next quarter."
    )
    why_now_prose = (
        "Walmart's Q3 2025 shrink callout puts the inventory accuracy "
        "conversation on the CFO's desk right now. Layered with the "
        "Symbotic rollout to 25 DCs, the next four quarters are an "
        "active automation window. Worth a touch this week."
    )

    ctx = AgentContext(account_name="Walmart", prior_results=priors)
    with patch.object(
        agent_mod,
        "synthesize_with_fallback",
        side_effect=_routed_synthesis(hook_prose, why_now_prose),
    ) as mock_synth:
        result = _run(ctx)

    assert isinstance(result, AgentResult)
    assert result.agent_name == AGENT_NAME
    assert result.section_title == SECTION_TITLE
    assert len(result.claims) == MAX_HOOKS + 1

    hook_claims = result.claims[:MAX_HOOKS]
    why_now = result.claims[-1]

    public_hooks = [c for c in hook_claims if c.source_tag is SourceTag.PUBLIC]
    assert 2 <= len(public_hooks) <= 3

    urls = {c.source_url for c in public_hooks}
    assert shrink_url in urls
    assert automation_url in urls

    for hook in public_hooks:
        # Mocked LLM prose, NOT the deterministic "Hook: ..." prefix.
        assert hook.text == hook_prose
        assert hook.source_url is not None
        assert hook.date is not None

    assert why_now.source_tag is SourceTag.INFERRED
    assert why_now.text == why_now_prose
    assert why_now.inference_logic is not None
    # The inference trace must name the contributing sections so the rep
    # can audit which buckets fed the urgency frame.
    assert "shrink" in why_now.inference_logic.lower()

    # LLM was called for each picked hook + once for Why Now.
    assert mock_synth.call_count == len(public_hooks) + 1


# ---------------------------------------------------------------------------
# 2. Sparse priors — GEODIS-style
# ---------------------------------------------------------------------------


def test_sparse_priors_geodis_one_hook_pad_with_tbd_and_why_now():
    """One dated signal → 1 PUBLIC hook + 2 HOOK TBD pads + Why Now."""
    url = "https://geodis.example/new-dc-atlanta"
    priors = [
        _wrap(
            "agent_2_operating_baseline",
            "Operating Baseline",
            [
                _public(
                    "GEODIS announced a new 800k sqft Atlanta DC "
                    "opening Q2 2026.",
                    url=url,
                    date=_months_ago(3),
                ),
            ],
        ),
        _wrap(
            "agent_5_shrink_compliance",
            "Shrink & Compliance",
            [
                Claim(
                    text="No public shrink data sourced.",
                    source_tag=SourceTag.NOT_FOUND,
                ),
            ],
        ),
    ]

    hook_prose = (
        "Saw GEODIS is opening an 800k sqft Atlanta DC in Q2 2026 — "
        "the inventory-accuracy baseline on greenfield builds is where "
        "we usually plug in."
    )
    why_now_prose = (
        "GEODIS is standing up an 800k sqft Atlanta facility in Q2 2026. "
        "Pre-launch is the cheapest window to lock in a continuous "
        "inventory baseline before the first pallet lands."
    )

    ctx = AgentContext(account_name="GEODIS", prior_results=priors)
    with patch.object(
        agent_mod,
        "synthesize_with_fallback",
        side_effect=_routed_synthesis(hook_prose, why_now_prose),
    ):
        result = _run(ctx)

    assert len(result.claims) == MAX_HOOKS + 1
    hook_claims = result.claims[:MAX_HOOKS]
    why_now = result.claims[-1]

    public_hooks = [c for c in hook_claims if c.source_tag is SourceTag.PUBLIC]
    tbd_hooks = [
        c for c in hook_claims
        if c.source_tag is SourceTag.NOT_FOUND
        and c.text == HOOK_TBD_TEXT
    ]
    assert len(public_hooks) == 1
    assert len(tbd_hooks) == 2
    assert public_hooks[0].source_url == url
    assert public_hooks[0].text == hook_prose

    assert why_now.source_tag is SourceTag.INFERRED
    assert why_now.text == why_now_prose


# ---------------------------------------------------------------------------
# 3. No dated priors — Notion-style — LLM is NOT called
# ---------------------------------------------------------------------------


def test_no_dated_priors_notion_does_not_call_llm():
    """Claims with no `date` → all hooks NOT_FOUND, Why Now NOT_FOUND,
    AND `synthesize_with_fallback` is never invoked."""
    priors = [
        _wrap(
            "agent_3_board_priorities",
            "Board Priorities",
            [
                _public(
                    "Notion mission statement on website.",
                    url="https://notion.example/about",
                    date=None,
                ),
            ],
        ),
    ]
    ctx = AgentContext(account_name="Notion", prior_results=priors)
    with patch.object(
        agent_mod,
        "synthesize_with_fallback",
        side_effect=_routed_synthesis("SHOULD NOT APPEAR", "SHOULD NOT APPEAR"),
    ) as mock_synth:
        result = _run(ctx)

    assert len(result.claims) == MAX_HOOKS + 1
    hook_claims = result.claims[:MAX_HOOKS]
    why_now = result.claims[-1]

    for hook in hook_claims:
        assert hook.source_tag is SourceTag.NOT_FOUND
        assert hook.text == HOOK_TBD_TEXT

    assert why_now.source_tag is SourceTag.NOT_FOUND
    assert "why now" in why_now.text.lower()

    # Critical: no LLM spend on a deterministic answer.
    assert mock_synth.call_count == 0


# ---------------------------------------------------------------------------
# 4. Stale priors filtered out (>12 months old) — LLM not called
# ---------------------------------------------------------------------------


def test_stale_priors_older_than_12_months_are_filtered_before_llm():
    """Dated claims older than 12 months must NOT become hooks AND must
    NOT trigger a synthesis call."""
    stale_url = "https://walmart.example/2020-shrink-report"
    priors = [
        _wrap(
            "agent_5_shrink_compliance",
            "Shrink & Compliance",
            [
                _public(
                    "Shrink event from 2020 — well outside freshness window.",
                    url=stale_url,
                    date="2020-06-15",
                ),
            ],
        ),
    ]
    ctx = AgentContext(account_name="Walmart", prior_results=priors)
    with patch.object(
        agent_mod,
        "synthesize_with_fallback",
        side_effect=_routed_synthesis("nope", "nope"),
    ) as mock_synth:
        result = _run(ctx)

    hook_claims = result.claims[:MAX_HOOKS]
    public_hooks = [c for c in hook_claims if c.source_tag is SourceTag.PUBLIC]
    assert public_hooks == []
    for hook in hook_claims:
        assert hook.source_tag is SourceTag.NOT_FOUND
        assert hook.text == HOOK_TBD_TEXT
    assert result.claims[-1].source_tag is SourceTag.NOT_FOUND
    # Filter ran before any LLM call.
    assert mock_synth.call_count == 0


# ---------------------------------------------------------------------------
# 5. Hook fallback path — synthesis returns the deterministic template
# ---------------------------------------------------------------------------


def test_hook_fallback_path_uses_deterministic_template():
    """When `synthesize_with_fallback` returns its fallback string, the
    hook claim must STILL emit using the deterministic template — no
    silent drop."""
    shrink_url = "https://walmart.example/q3-2025-earnings"
    priors = [
        _wrap(
            "agent_5_shrink_compliance",
            "Shrink & Compliance",
            [
                _public(
                    "Q3 2025 earnings call: CFO flagged $3B shrink write-offs.",
                    url=shrink_url,
                    date=_months_ago(2),
                ),
            ],
        ),
    ]
    ctx = AgentContext(account_name="Walmart", prior_results=priors)
    with patch.object(
        agent_mod,
        "synthesize_with_fallback",
        side_effect=_fallback_synthesis(),
    ):
        result = _run(ctx)

    hook_claims = result.claims[:MAX_HOOKS]
    public_hooks = [c for c in hook_claims if c.source_tag is SourceTag.PUBLIC]
    assert len(public_hooks) == 1
    # Deterministic format restored.
    assert public_hooks[0].text.lower().startswith("hook:")
    assert "shrink" in public_hooks[0].text.lower()
    assert "anchor:" in public_hooks[0].text.lower()
    assert public_hooks[0].source_url == shrink_url


# ---------------------------------------------------------------------------
# 6. Why Now fallback path — synthesis returns the deterministic template
# ---------------------------------------------------------------------------


def test_why_now_fallback_path_uses_deterministic_template():
    """When `synthesize_with_fallback` returns the fallback, Why Now must
    emit using the deterministic 2-sentence template."""
    shrink_url = "https://walmart.example/q3-2025-earnings"
    priors = [
        _wrap(
            "agent_5_shrink_compliance",
            "Shrink & Compliance",
            [
                _public(
                    "Q3 2025 earnings call: CFO flagged $3B shrink write-offs.",
                    url=shrink_url,
                    date=_months_ago(2),
                ),
            ],
        ),
    ]
    ctx = AgentContext(account_name="Walmart", prior_results=priors)
    with patch.object(
        agent_mod,
        "synthesize_with_fallback",
        side_effect=_fallback_synthesis(),
    ):
        result = _run(ctx)

    why_now = result.claims[-1]
    assert why_now.source_tag is SourceTag.INFERRED
    text = why_now.text.lower()
    assert text.startswith("why now:")
    # Deterministic second sentence boilerplate is present.
    assert "fresh, dated signal" in text
    assert why_now.inference_logic is not None


# ---------------------------------------------------------------------------
# 7. Why Now picks highest-priority section over freshness
# ---------------------------------------------------------------------------


def test_why_now_picks_highest_priority_section_over_freshness():
    """Shrink (priority 1) beats Operating Baseline (priority 5) even when
    the latter is fresher."""
    shrink_url = "https://walmart.example/q1-2025-shrink-flag"
    leadership_url = "https://walmart.example/new-vp-supply-chain"

    priors = [
        _wrap(
            "agent_5_shrink_compliance",
            "Shrink & Compliance",
            [
                _public(
                    "Shrink containment flagged as a top board priority "
                    "on the Q1 2025 earnings call.",
                    url=shrink_url,
                    date=_months_ago(6),
                ),
            ],
        ),
        _wrap(
            "agent_2_operating_baseline",
            "Operating Baseline",
            [
                _public(
                    "New VP of Supply Chain hired in April 2026.",
                    url=leadership_url,
                    date=_months_ago(1),
                ),
            ],
        ),
    ]
    ctx = AgentContext(account_name="Walmart", prior_results=priors)
    with patch.object(
        agent_mod,
        "synthesize_with_fallback",
        side_effect=_fallback_synthesis(),
    ):
        result = _run(ctx)

    why_now = result.claims[-1]
    assert why_now.source_tag is SourceTag.INFERRED
    assert why_now.source_url == shrink_url
    assert "shrink" in why_now.text.lower()


# ---------------------------------------------------------------------------
# 8. Hook system prompt forbids generic openers
# ---------------------------------------------------------------------------


def test_hook_system_prompt_quotes_sdr_rule_against_generic_openers():
    """The hook synthesis prompt must include the literal SDR-skill rule
    so the LLM cannot rationalize a generic line."""
    prompt = _hook_system_prompt()
    assert GENERIC_OPENER_BAN in prompt
    # And the literal phrase from the worker prompt + SDR skill.
    assert (
        "Do not write hooks that could apply to any logistics company."
        in prompt
    )
    # Grounding stanza pieces must be present (anti-AI-tell vocab + no
    # em-dashes + no fabrication).
    assert "leverage" in prompt  # forbidden vocab list embedded
    assert "Do not invent facts" in prompt
    assert "no greeting" in prompt.lower()


# ---------------------------------------------------------------------------
# 9. Why Now system prompt forbids generic openers
# ---------------------------------------------------------------------------


def test_why_now_system_prompt_quotes_sdr_rule_against_generic_openers():
    """The Why Now synthesis prompt must also include the literal SDR-skill
    anti-generic rule so the urgency line stays account-specific."""
    prompt = _why_now_system_prompt()
    assert GENERIC_OPENER_BAN in prompt
    assert (
        "Do not write hooks that could apply to any logistics company."
        in prompt
    )
    assert "Do not invent facts" in prompt
    # The role description must clearly be an AE (not SDR) for Why Now.
    assert "AE" in prompt


# ---------------------------------------------------------------------------
# 10. Schema round-trip — every claim serializes cleanly
# ---------------------------------------------------------------------------


def test_schema_round_trip_all_claims_serialize_cleanly():
    """Every emitted claim must pass Claim.__post_init__ and to_dict()."""
    priors = [
        _wrap(
            "agent_5_shrink_compliance",
            "Shrink & Compliance",
            [
                _public(
                    "Shrink write-offs at $3B in Q3 2025.",
                    url="https://example.com/a",
                    date=_months_ago(3),
                ),
            ],
        ),
        _wrap(
            "agent_6_automation_stack",
            "Automation Stack",
            [
                _public(
                    "Symbotic to 25 DCs.",
                    url="https://example.com/b",
                    date=_months_ago(5),
                ),
            ],
        ),
    ]
    ctx = AgentContext(account_name="Walmart", prior_results=priors)
    with patch.object(
        agent_mod,
        "synthesize_with_fallback",
        side_effect=_routed_synthesis(
            "Saw the $3B shrink callout in Q3 — wanted to share what we do.",
            "Walmart's Q3 shrink callout and the Symbotic expansion put the "
            "inventory baseline conversation on the table this quarter.",
        ),
    ):
        result = _run(ctx)
    payload = result.to_dict()

    assert payload["agent_name"] == AGENT_NAME
    assert payload["section_title"] == SECTION_TITLE
    assert isinstance(payload["claims"], list)
    assert len(payload["claims"]) == MAX_HOOKS + 1

    for c in payload["claims"]:
        assert "text" in c and isinstance(c["text"], str) and c["text"]
        assert "source_tag" in c


# ---------------------------------------------------------------------------
# 11. industry_pain_result is ignored as a hook source
# ---------------------------------------------------------------------------


def test_industry_pain_result_is_not_used_as_hook_source():
    """Agent 9 output is for vocabulary only — its claims are NOT eligible
    to become hooks, and the LLM is NOT called when no other priors exist."""
    industry = _wrap(
        "agent_9_industry_pain",
        "Industry Pain Matcher",
        [
            _public(
                "Industry-wide cold-chain shrink benchmark of 2.5%.",
                url="https://industry.example/benchmark",
                date=_months_ago(1),
            ),
        ],
    )
    ctx = AgentContext(
        account_name="Generic 3PL",
        prior_results=[],
        industry_pain_result=industry,
    )
    with patch.object(
        agent_mod,
        "synthesize_with_fallback",
        side_effect=_routed_synthesis("nope", "nope"),
    ) as mock_synth:
        result = _run(ctx)

    hook_claims = result.claims[:MAX_HOOKS]
    for hook in hook_claims:
        assert hook.source_tag is SourceTag.NOT_FOUND
        assert hook.text == HOOK_TBD_TEXT
    assert result.claims[-1].source_tag is SourceTag.NOT_FOUND
    assert mock_synth.call_count == 0
