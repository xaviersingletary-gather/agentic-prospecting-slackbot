"""Phase 20 — Agent 1 (Network Footprint) tests.

Covers the four mandatory cases from `docs/agent-contract.md`:

1. Walmart  — PUBLIC claim with source_url (mocked 10-K / press snippet).
2. GEODIS  — INFERRED claim using 3PL sqft benchmark when no sqft sourced.
3. Notion  — NOT_FOUND claim when Exa returns nothing relevant.
4. Error   — ExaSearchClient.search raises; AgentResult has a single ERROR
            claim and the log line carries `type(e).__name__` only.

External clients are mocked at this module's import path. No real Exa
calls are made.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from src.research.agents.agent_1_network_footprint import (
    AGENT_NAME,
    SECTION_TITLE,
    AgentContext,
    run,
)
from src.research.agents.contract import AgentResult, SourceTag


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(side_effect_by_query: Dict[str, List[Dict[str, Any]]]) -> MagicMock:
    """Build a MagicMock ExaSearchClient whose .search() returns results
    based on a substring match against `query`. Anything unmatched → []."""

    def _fake_search(query: str, *args, **kwargs):
        for needle, payload in side_effect_by_query.items():
            if needle.lower() in query.lower():
                return payload
        return []

    mock = MagicMock()
    mock.search.side_effect = _fake_search
    return mock


def _claim_tags(result: AgentResult) -> List[SourceTag]:
    return [c.source_tag for c in result.claims]


# ---------------------------------------------------------------------------
# Walmart — public retail giant; DC count is public information
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_walmart_emits_public_dc_count_with_source_url():
    """Mocked 10-K + press snippets mention ~210 DCs.

    The agent should pick up the integer count and emit a PUBLIC Claim
    pointing at a real URL.
    """
    walmart_results = {
        "distribution centers": [
            {
                "title": "Walmart 2024 Annual Report",
                "url": "https://corporate.walmart.com/annual-report-2024",
                "snippet": (
                    "As of fiscal 2024, Walmart operates 210 distribution "
                    "centers across the United States, supporting more than "
                    "4,600 stores."
                ),
                "published_date": "2024-03-15",
            },
            {
                "title": "Walmart announces new fulfillment center",
                "url": "https://corporate.walmart.com/news/2024/dc-opening",
                "snippet": "Walmart's network now includes 211 DCs nationwide.",
                "published_date": "2024-05-01",
            },
        ],
        "square feet": [
            {
                "title": "Walmart 10-K filing 2024",
                "url": "https://www.sec.gov/walmart-10k",
                "snippet": (
                    "Walmart's owned and leased warehouse footprint totals "
                    "approximately 175 million square feet."
                ),
                "published_date": "2024-03-31",
            }
        ],
        "locations": [
            {
                "title": "Walmart breaks ground on Texas DC",
                "url": "https://corporate.walmart.com/news/tx-dc",
                "snippet": "1.2 million sq ft Texas distribution center planned.",
                "published_date": "2024-06-12",
            }
        ],
    }
    client = _make_client(walmart_results)
    ctx = AgentContext(
        account_name="Walmart",
        account_domain="walmart.com",
        industry="retail",
        exa_client=client,
    )

    result = await run(ctx)

    assert result.agent_name == AGENT_NAME
    assert result.section_title == SECTION_TITLE
    # At least one PUBLIC claim must carry a source_url.
    public_claims = [c for c in result.claims if c.source_tag is SourceTag.PUBLIC]
    assert public_claims, f"expected a PUBLIC claim, got {_claim_tags(result)}"
    assert any(c.source_url for c in public_claims)
    # The DC-count claim should reference the larger of the two numbers.
    assert any("211" in c.text or "210" in c.text for c in public_claims)
    # Sqft should also be PUBLIC since the snippet had a hard number.
    assert any("175,000,000" in c.text or "sq ft" in c.text.lower() for c in public_claims)
    # Round-trip the dataclasses.
    for c in result.claims:
        assert c.to_dict()["text"]


# ---------------------------------------------------------------------------
# GEODIS — private 3PL; no public sqft, industry benchmark fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_geodis_infers_sqft_via_3pl_benchmark():
    """Company-site snippets mention 'over 300 facilities worldwide' but no
    square-footage number. Industry=3pl → INFERRED sqft claim using the
    400k sqft/DC benchmark, with inference_logic populated.
    """
    geodis_results = {
        "distribution centers": [
            {
                "title": "GEODIS — Global Logistics Network",
                "url": "https://geodis.com/network",
                "snippet": (
                    "GEODIS operates more than 300 warehouses worldwide "
                    "as part of its contract logistics offering."
                ),
                "published_date": "2024-02-10",
            }
        ],
        # No sqft snippet returned — sqft search is empty.
    }
    client = _make_client(geodis_results)
    ctx = AgentContext(
        account_name="GEODIS",
        account_domain="geodis.com",
        industry="3PL",
        exa_client=client,
    )

    result = await run(ctx)

    tags = _claim_tags(result)
    assert SourceTag.INFERRED in tags, tags
    inferred = [c for c in result.claims if c.source_tag is SourceTag.INFERRED]
    # Should include both the sqft inference and the pallet-position
    # inference; both must carry inference_logic.
    assert all(c.inference_logic for c in inferred)
    # At least one inferred claim references the 3pl benchmark.
    assert any("3pl" in (c.inference_logic or "").lower() for c in inferred)
    # No silent NOT_FOUND for sqft when we can infer it.
    sqft_claims = [c for c in result.claims if "sq ft" in c.text.lower() or "sqft" in c.text.lower()]
    assert any(c.source_tag is SourceTag.INFERRED for c in sqft_claims)
    # Pallet estimate also INFERRED with the formula spelled out.
    pallet_claims = [c for c in result.claims if "pallet" in c.text.lower()]
    assert pallet_claims
    assert all(c.source_tag is SourceTag.INFERRED for c in pallet_claims)
    assert all(
        "× 0.60 × 4 / 36" in (c.inference_logic or "") for c in pallet_claims
    )


# ---------------------------------------------------------------------------
# Notion — outside-ICP SaaS control; nothing useful in Exa
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notion_emits_not_found_when_nothing_relevant():
    """No DC count, no sqft, no industry hint → at least one NOT_FOUND."""
    client = _make_client({})  # every search returns []
    ctx = AgentContext(
        account_name="Notion",
        account_domain="notion.so",
        exa_client=client,
    )

    result = await run(ctx)

    tags = _claim_tags(result)
    assert SourceTag.NOT_FOUND in tags, tags
    # No PUBLIC / INFERRED claims should sneak through when there's no
    # data and no industry to benchmark against.
    assert SourceTag.PUBLIC not in tags
    assert SourceTag.INFERRED not in tags
    # Section is still rendered — claims list non-empty.
    assert result.claims
    assert result.agent_name == AGENT_NAME


# ---------------------------------------------------------------------------
# Error path — Exa client raises; agent must NOT re-raise
# ---------------------------------------------------------------------------


class _FakeBoom(RuntimeError):
    """Distinctive exception type so we can assert the log captures its
    name and only its name."""


@pytest.mark.asyncio
async def test_error_path_logs_type_name_only(caplog):
    """All three Exa searches raise → AgentResult.error(...) result with
    exactly one ERROR claim. The log line carries `type(e).__name__` and
    never the str of the exception (which contains a fake secret).
    """
    secret = "sk-leak-do-not-print-this-12345"

    client = MagicMock()
    client.search.side_effect = _FakeBoom(secret)

    ctx = AgentContext(
        account_name="ExplodingCo",
        exa_client=client,
    )

    with caplog.at_level(logging.ERROR):
        result = await run(ctx)

    assert isinstance(result, AgentResult)
    assert len(result.claims) == 1
    assert result.claims[0].source_tag is SourceTag.ERROR

    # The exception type name MUST appear; the secret payload MUST NOT.
    joined = " ".join(rec.getMessage() for rec in caplog.records)
    assert "_FakeBoom" in joined
    assert secret not in joined


# ---------------------------------------------------------------------------
# Schema enforcement — every claim round-trips to_dict()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_every_claim_roundtrips_to_dict():
    """Sanity check: nothing the agent constructs trips Claim.__post_init__."""
    client = _make_client({
        "distribution centers": [
            {
                "title": "ACME",
                "url": "https://acme.example.com/dc-network",
                "snippet": "ACME runs 22 distribution centers.",
                "published_date": "2024-01-01",
            }
        ],
        "square feet": [
            {
                "title": "ACME",
                "url": "https://acme.example.com/footprint",
                "snippet": "ACME has 12 million square feet of warehousing.",
                "published_date": "2024-01-01",
            }
        ],
    })
    ctx = AgentContext(account_name="Acme", industry="manufacturing", exa_client=client)
    result = await run(ctx)
    for c in result.claims:
        d = c.to_dict()
        assert d["text"]
        assert d["source_tag"] in {t.value for t in SourceTag}
