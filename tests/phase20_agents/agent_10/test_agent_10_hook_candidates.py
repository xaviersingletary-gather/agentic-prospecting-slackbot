"""Tests for Agent 10 — Hook Candidates + Why Now.

Agent 10 has no external dependencies — it synthesizes the outputs of
agents 1–6 into 2–3 cold-outreach opener angles plus a "Why Now" line.

Coverage:

  1. Rich-priors (Walmart-style): shrink event + automation deployment,
     both dated and sourced. Expect 2–3 PUBLIC hooks plus one INFERRED
     Why Now citing the freshest highest-priority signal.
  2. Sparse-priors (GEODIS-style): a single dated PUBLIC claim. Expect
     1 PUBLIC hook + 2 NOT_FOUND "HOOK TBD" pads + an INFERRED Why Now.
  3. No-dated-priors (Notion-style): claims present but with no `date`
     field. Expect 3 NOT_FOUND HOOK TBD claims + a NOT_FOUND Why Now.
  4. Stale-priors filtered out: dates older than 12 months are dropped.
  5. Why Now prefers highest-priority over freshest: a 6-month-old shrink
     event beats a more recent leadership change.
  6. Schema round-trip: every claim survives `.to_dict()` cleanly.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import pytest

from src.research.agents.agent_10_hook_candidates import (
    AGENT_NAME,
    HOOK_TBD_TEXT,
    MAX_HOOKS,
    SECTION_TITLE,
    AgentContext,
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


# ---------------------------------------------------------------------------
# 1. Rich priors — Walmart-style
# ---------------------------------------------------------------------------


def test_rich_priors_walmart_emits_two_public_hooks_and_inferred_why_now():
    """Two dated triggers (shrink + automation) → 2 PUBLIC hooks + Why Now."""
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
    ctx = AgentContext(account_name="Walmart", prior_results=priors)
    result = _run(ctx)

    assert isinstance(result, AgentResult)
    assert result.agent_name == AGENT_NAME
    assert result.section_title == SECTION_TITLE

    # Should be exactly MAX_HOOKS hook slots + 1 Why Now = MAX_HOOKS + 1.
    assert len(result.claims) == MAX_HOOKS + 1

    hook_claims = result.claims[:MAX_HOOKS]
    why_now = result.claims[-1]

    public_hooks = [c for c in hook_claims if c.source_tag is SourceTag.PUBLIC]
    assert 2 <= len(public_hooks) <= 3, (
        "rich priors should yield 2–3 grounded hooks"
    )

    # Every PUBLIC hook should carry the underlying source_url + date.
    urls = {c.source_url for c in public_hooks}
    assert shrink_url in urls
    assert automation_url in urls
    for hook in public_hooks:
        assert hook.text.lower().startswith("hook:")
        assert hook.source_url is not None
        assert hook.date is not None

    # Why Now: INFERRED + references the highest-priority (shrink) signal.
    assert why_now.source_tag is SourceTag.INFERRED
    assert "why now" in why_now.text.lower()
    assert "shrink" in why_now.text.lower()
    assert why_now.inference_logic is not None
    assert "shrink" in why_now.inference_logic.lower()


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
        # Add a couple of upstream gaps to confirm they don't pollute.
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
    ctx = AgentContext(account_name="GEODIS", prior_results=priors)
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

    # Why Now references the single dated signal.
    assert why_now.source_tag is SourceTag.INFERRED
    assert "atlanta" in why_now.text.lower() or "operating baseline" in why_now.text.lower()


# ---------------------------------------------------------------------------
# 3. No dated priors — Notion-style
# ---------------------------------------------------------------------------


def test_no_dated_priors_notion_emits_three_tbd_and_not_found_why_now():
    """Claims with no `date` → all hooks NOT_FOUND, Why Now NOT_FOUND."""
    priors = [
        _wrap(
            "agent_3_board_priorities",
            "Board Priorities",
            [
                # PUBLIC but no `date` field — must be skipped.
                _public(
                    "Notion mission statement on website.",
                    url="https://notion.example/about",
                    date=None,
                ),
            ],
        ),
    ]
    ctx = AgentContext(account_name="Notion", prior_results=priors)
    result = _run(ctx)

    assert len(result.claims) == MAX_HOOKS + 1
    hook_claims = result.claims[:MAX_HOOKS]
    why_now = result.claims[-1]

    for hook in hook_claims:
        assert hook.source_tag is SourceTag.NOT_FOUND
        assert hook.text == HOOK_TBD_TEXT

    assert why_now.source_tag is SourceTag.NOT_FOUND
    assert "why now" in why_now.text.lower()


# ---------------------------------------------------------------------------
# 4. Stale priors filtered out (>12 months old)
# ---------------------------------------------------------------------------


def test_stale_priors_older_than_12_months_are_filtered():
    """Dated claims older than 12 months must NOT become hooks."""
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
    result = _run(ctx)

    hook_claims = result.claims[:MAX_HOOKS]
    public_hooks = [c for c in hook_claims if c.source_tag is SourceTag.PUBLIC]
    assert public_hooks == [], "stale claims must not become hooks"

    # All three slots should be HOOK TBD; Why Now should be NOT_FOUND.
    for hook in hook_claims:
        assert hook.source_tag is SourceTag.NOT_FOUND
        assert hook.text == HOOK_TBD_TEXT

    assert result.claims[-1].source_tag is SourceTag.NOT_FOUND


# ---------------------------------------------------------------------------
# 5. Why Now picks highest-priority section over freshness
# ---------------------------------------------------------------------------


def test_why_now_picks_highest_priority_section_over_freshness():
    """Shrink (priority 1) beats Operating Baseline (priority 5) even when
    the latter is fresher."""
    shrink_url = "https://walmart.example/q1-2025-shrink-flag"
    leadership_url = "https://walmart.example/new-vp-supply-chain"

    priors = [
        # 6 months old, highest-priority section.
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
        # 1 month old, lowest-priority section.
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
    result = _run(ctx)

    why_now = result.claims[-1]
    assert why_now.source_tag is SourceTag.INFERRED
    # Why Now must anchor on the SHRINK signal — i.e. its URL/section.
    assert why_now.source_url == shrink_url, (
        "Why Now should anchor on highest-priority section, not freshest"
    )
    assert "shrink" in why_now.text.lower()


# ---------------------------------------------------------------------------
# 6. Schema round-trip — every claim serializes cleanly
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
    result = _run(ctx)
    payload = result.to_dict()

    assert payload["agent_name"] == AGENT_NAME
    assert payload["section_title"] == SECTION_TITLE
    assert isinstance(payload["claims"], list)
    assert len(payload["claims"]) == MAX_HOOKS + 1

    # Every claim dict must carry source_tag, text — required core fields.
    for c in payload["claims"]:
        assert "text" in c and isinstance(c["text"], str) and c["text"]
        assert "source_tag" in c


# ---------------------------------------------------------------------------
# 7. Bonus: industry_pain_result is ignored as a hook source
# ---------------------------------------------------------------------------


def test_industry_pain_result_is_not_used_as_hook_source():
    """Agent 9 output is for vocabulary only — its claims are NOT eligible
    to become hooks."""
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
    # No prior_results at all — only industry_pain_result is set. All
    # hook slots should fall back to HOOK TBD and Why Now to NOT_FOUND.
    ctx = AgentContext(
        account_name="Generic 3PL",
        prior_results=[],
        industry_pain_result=industry,
    )
    result = _run(ctx)

    hook_claims = result.claims[:MAX_HOOKS]
    for hook in hook_claims:
        assert hook.source_tag is SourceTag.NOT_FOUND
        assert hook.text == HOOK_TBD_TEXT
    assert result.claims[-1].source_tag is SourceTag.NOT_FOUND
