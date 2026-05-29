"""Phase 20 — Agent 1 Exception Tax extension tests.

Covers the four cases from the worker brief:

1. Walmart-style PUBLIC sqft → INFERRED Exception Tax claim with the
   pre-formatted `math_shown` formula in `inference_logic`.
2. GEODIS-style INFERRED sqft (industry-benchmark fallback) → INFERRED
   Exception Tax claim that mentions the sqft-estimate provenance in
   `inference_logic`.
3. Notion-style no sqft signal → exactly one NOT_FOUND Exception Tax
   claim with the spec'd fallback text.
4. Schema round-trip — every Exception Tax claim passes
   `Claim.__post_init__` and serializes cleanly via `.to_dict()`.

External clients are mocked at this module's import path. No real Exa
calls are made.
"""
from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from src.research.agents.agent_1_network_footprint import (
    AgentContext,
    run,
)
from src.research.agents.contract import AgentResult, Claim, SourceTag


# ---------------------------------------------------------------------------
# Helpers (mirrors test_network_footprint.py to keep mocks consistent)
# ---------------------------------------------------------------------------


def _make_client(side_effect_by_query: Dict[str, List[Dict[str, Any]]]) -> MagicMock:
    def _fake_search(query: str, *args, **kwargs):
        for needle, payload in side_effect_by_query.items():
            if needle.lower() in query.lower():
                return payload
        return []

    mock = MagicMock()
    mock.search.side_effect = _fake_search
    return mock


def _exception_tax_claims(result: AgentResult) -> List[Claim]:
    """Pull out the Exception Tax claims by text marker.

    The fallback claim is NOT_FOUND with 'Exception Tax not computed'; the
    real-math claim text begins with 'Exception Tax:' — either way, the
    leading 'Exception Tax' token uniquely identifies the extension's
    output without coupling to other agent claims.
    """
    return [c for c in result.claims if c.text.lower().startswith("exception tax")]


# ---------------------------------------------------------------------------
# 1. Walmart — PUBLIC sqft → INFERRED Exception Tax with math_shown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_walmart_public_sqft_yields_inferred_exception_tax():
    """A PUBLIC sqft figure (~175M sq ft) should still produce an
    INFERRED Exception Tax claim — the math itself is inferred even when
    the input sqft was sourced. The `inference_logic` must contain the
    full pre-formatted math (positions formula + savings formula).
    """
    walmart_results = {
        "distribution centers": [
            {
                "title": "Walmart 2024 Annual Report",
                "url": "https://corporate.walmart.com/annual-report-2024",
                "snippet": (
                    "As of fiscal 2024, Walmart operates 210 distribution "
                    "centers across the United States."
                ),
                "published_date": "2024-03-15",
            }
        ],
        "square feet": [
            {
                "title": "Walmart 10-K filing 2024",
                "url": "https://www.sec.gov/walmart-10k",
                "snippet": (
                    "Walmart's owned and leased warehouse footprint totals "
                    "approximately 175,000,000 square feet."
                ),
                "published_date": "2024-03-31",
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

    tax_claims = _exception_tax_claims(result)
    assert len(tax_claims) == 1, [c.text for c in tax_claims]
    tax = tax_claims[0]

    # Even though sqft was PUBLIC, the savings number is INFERRED.
    assert tax.source_tag is SourceTag.INFERRED
    # Text format: "Exception Tax: ~$XM/year conservative annual savings"
    assert "$" in tax.text and "M/year" in tax.text
    assert "conservative annual savings" in tax.text
    # Pre-formatted math from calculate_exception_tax must be present.
    assert tax.inference_logic is not None
    assert "× 0.60 × 4 / 36" in tax.inference_logic
    assert "Pallet positions" in tax.inference_logic
    assert "2.5% error rate" in tax.inference_logic


# ---------------------------------------------------------------------------
# 2. GEODIS — INFERRED sqft (benchmark fallback) → INFERRED Exception Tax
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_geodis_inferred_sqft_yields_inferred_exception_tax():
    """GEODIS has a DC count and an industry hint but no public sqft. The
    network-footprint agent emits an INFERRED sqft claim via the 3PL
    benchmark; the Exception Tax claim should chain off it and note that
    the sqft figure was estimated rather than sourced.
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
        # No square-feet snippets — forces the industry-benchmark fallback.
    }
    client = _make_client(geodis_results)
    ctx = AgentContext(
        account_name="GEODIS",
        account_domain="geodis.com",
        industry="3PL",
        exa_client=client,
    )

    result = await run(ctx)

    # Sanity: the agent should have emitted both an INFERRED sqft claim
    # AND an INFERRED Exception Tax claim derived from it.
    sqft_inferred = [
        c for c in result.claims
        if c.source_tag is SourceTag.INFERRED
        and ("sq ft" in c.text.lower() or "sqft" in c.text.lower())
    ]
    assert sqft_inferred, "expected an INFERRED sqft claim from the 3PL benchmark"

    tax_claims = _exception_tax_claims(result)
    assert len(tax_claims) == 1
    tax = tax_claims[0]
    assert tax.source_tag is SourceTag.INFERRED
    assert tax.inference_logic is not None
    # The savings line should still carry the math_shown formula AND the
    # sqft-estimate provenance language from the worker brief.
    assert "× 0.60 × 4 / 36" in tax.inference_logic
    assert "sqft estimate" in tax.inference_logic.lower()
    assert "exception-tax formula" in tax.inference_logic.lower()


# ---------------------------------------------------------------------------
# 3. Notion — no sqft anywhere → exactly one NOT_FOUND Exception Tax claim
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notion_emits_not_found_exception_tax():
    """A SaaS account with no warehouse footprint should still produce a
    section — but the Exception Tax line must be a NOT_FOUND with the
    spec'd fallback text so the AE knows the math wasn't skipped, it
    just couldn't be computed.
    """
    client = _make_client({})  # every Exa search returns []
    ctx = AgentContext(
        account_name="Notion",
        account_domain="notion.so",
        exa_client=client,
    )

    result = await run(ctx)

    tax_claims = _exception_tax_claims(result)
    assert len(tax_claims) == 1, [c.text for c in tax_claims]
    tax = tax_claims[0]
    assert tax.source_tag is SourceTag.NOT_FOUND
    assert tax.text == (
        "Exception Tax not computed — no warehouse square footage available."
    )
    # NOT_FOUND claims must NOT carry source_url or inference_logic.
    assert tax.source_url is None
    assert tax.inference_logic is None


# ---------------------------------------------------------------------------
# 4. Schema round-trip — Claim.__post_init__ + .to_dict() both clean
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exception_tax_claim_roundtrips_to_dict():
    """Run the agent against the Walmart fixture and confirm the
    Exception Tax claim survives `Claim.__post_init__` validation and
    serializes through `.to_dict()` without dropping required fields."""
    walmart_results = {
        "square feet": [
            {
                "title": "Walmart 10-K filing 2024",
                "url": "https://www.sec.gov/walmart-10k",
                "snippet": (
                    "Walmart's warehouse footprint totals approximately "
                    "175,000,000 square feet."
                ),
                "published_date": "2024-03-31",
            }
        ],
    }
    client = _make_client(walmart_results)
    ctx = AgentContext(
        account_name="Walmart",
        industry="retail",
        exa_client=client,
    )
    result = await run(ctx)

    tax_claims = _exception_tax_claims(result)
    assert tax_claims
    tax = tax_claims[0]

    # __post_init__ would have raised on construction if the shape were
    # wrong; re-constructing from the dict is the strongest round-trip we
    # can do without re-implementing the contract.
    d = tax.to_dict()
    assert d["text"]
    assert d["source_tag"] == SourceTag.INFERRED.value
    assert d["inference_logic"]
    # Re-hydrating the dict back into a Claim must not raise either.
    rehydrated = Claim(
        text=d["text"],
        source_tag=SourceTag(d["source_tag"]),
        source_url=d.get("source_url"),
        inference_logic=d.get("inference_logic"),
        source=d.get("source"),
        date=d.get("date"),
    )
    assert rehydrated.text == tax.text
    assert rehydrated.source_tag is SourceTag.INFERRED
