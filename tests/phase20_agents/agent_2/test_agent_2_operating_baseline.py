"""Phase 20 — Agent 2 (Operating Baseline) tests.

Covers the four required cases from the agent-contract briefing:

1. Walmart (public retail) — EDGAR 10-K hit + Exa growth signal.
2. GEODIS (private 3PL) — EDGAR returns None, Exa returns realistic news.
3. Notion (outside-ICP control) — both clients return nothing → all NOT_FOUND.
4. Error path — Exa raises; result still well-formed with ERROR claims.

External clients are mocked. No real HTTP.
"""
from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from src.research.agents.agent_2_operating_baseline import (
    AGENT_NAME,
    SECTION_TITLE,
    AgentContext,
    run,
)
from src.research.agents.contract import AgentResult, Claim, SourceTag


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _exa_with(results_by_query: Dict[str, List[Dict[str, Any]]]):
    """Build a mock ExaSearchClient whose `.search(query, ...)` returns the
    list matched by a substring of the query keyword."""
    client = MagicMock()

    def _search(query: str, **kwargs):
        for needle, results in results_by_query.items():
            if needle.lower() in query.lower():
                return results
        return []

    client.search.side_effect = _search
    return client


def _edgar_with(filing):
    client = MagicMock()
    client.find_latest_10k.return_value = filing
    return client


def _by_tag(result: AgentResult, tag: SourceTag) -> List[Claim]:
    return [c for c in result.claims if c.source_tag is tag]


# ---------------------------------------------------------------------------
# 1. Walmart — public retail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_walmart_public_revenue_and_growth_signal():
    edgar = _edgar_with(
        {
            "entity_name": "WALMART INC.",
            "file_date": "2026-03-21",
            "period": "2026-01-31",
            "cik": 104169,
            "accession_no": "0000104169-26-000012",
            "document_url": (
                "https://www.sec.gov/Archives/edgar/data/104169/"
                "000010416926000012/wmt-20260131.htm"
            ),
            "form_type": "10-K",
        }
    )
    exa = _exa_with(
        {
            "expansion": [
                {
                    "title": "Walmart opens 5 new automated DCs in 2026",
                    "url": "https://corporate.walmart.com/news/2026/04/01/expansion",
                    "snippet": (
                        "Walmart announced an expansion of its supply chain with "
                        "five new distribution center openings across the US."
                    ),
                    "published_date": "2026-04-01",
                }
            ],
            "employees": [
                {
                    "title": "Walmart workforce",
                    "url": "https://corporate.walmart.com/about",
                    "snippet": "Walmart has 2,100,000 employees worldwide.",
                    "published_date": "2026-02-10",
                }
            ],
        }
    )

    ctx = AgentContext(
        account_name="Walmart",
        edgar_client=edgar,
        exa_client=exa,
    )
    result = await run(ctx)

    assert result.agent_name == AGENT_NAME
    assert result.section_title == SECTION_TITLE
    # Round-trip check
    assert result.to_dict()["agent_name"] == AGENT_NAME

    public = _by_tag(result, SourceTag.PUBLIC)
    # At least one PUBLIC revenue claim sourced from the EDGAR doc URL.
    assert any(
        "sec.gov" in (c.source_url or "") and "10-K" in (c.text or "")
        for c in public
    ), f"Expected EDGAR-sourced revenue claim; got {[c.to_dict() for c in public]}"

    # At least one growth-signal PUBLIC claim.
    assert any(
        "walmart.com" in (c.source_url or "").lower()
        and (
            "expansion" in (c.text or "").lower()
            or "distribution center" in (c.text or "").lower()
        )
        for c in public
    ), "Expected an expansion / DC growth-signal PUBLIC claim"


# ---------------------------------------------------------------------------
# 2. GEODIS — private 3PL, EDGAR returns None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_geodis_private_revenue_from_news_and_headcount():
    edgar = _edgar_with(None)  # No SEC filing — private foreign 3PL
    exa = _exa_with(
        {
            "revenue": [
                {
                    "title": "GEODIS reports record year",
                    "url": "https://www.geodis.com/press/2026/annual-results",
                    "snippet": (
                        "GEODIS reported revenue of $11.2 billion in fiscal year 2025, "
                        "up 6% year over year."
                    ),
                    "published_date": "2026-03-15",
                }
            ],
            "employees": [
                {
                    "title": "GEODIS company profile",
                    "url": "https://www.geodis.com/about",
                    "snippet": (
                        "GEODIS employs 53,000 employees across more than 60 countries."
                    ),
                    "published_date": "2026-01-12",
                }
            ],
            "expansion": [
                {
                    "title": "GEODIS opens new DC in Memphis",
                    "url": "https://www.geodis.com/press/memphis-dc",
                    "snippet": "GEODIS opened a new distribution center in Memphis.",
                    "published_date": "2026-02-20",
                }
            ],
        }
    )

    ctx = AgentContext(
        account_name="GEODIS",
        edgar_client=edgar,
        exa_client=exa,
    )
    result = await run(ctx)

    public = _by_tag(result, SourceTag.PUBLIC)
    # Revenue PUBLIC claim sourced from news, NOT from sec.gov.
    revenue_claims = [
        c
        for c in public
        if "revenue" in (c.text or "").lower() and "geodis.com" in (c.source_url or "")
    ]
    assert revenue_claims, (
        f"Expected a news-sourced revenue claim; got {[c.to_dict() for c in public]}"
    )
    assert "$11.2" in revenue_claims[0].text or "11.2" in revenue_claims[0].text

    # Headcount PUBLIC claim.
    headcount_claims = [
        c
        for c in public
        if "53,000" in (c.text or "") or "employees" in (c.text or "").lower()
    ]
    assert headcount_claims, "Expected a headcount PUBLIC claim"
    assert all(c.source_url for c in headcount_claims)


# ---------------------------------------------------------------------------
# 3. Notion — outside ICP, both clients return nothing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notion_outside_icp_explicit_not_found():
    edgar = _edgar_with(None)
    exa = _exa_with({})  # All queries return empty list

    ctx = AgentContext(
        account_name="Notion",
        edgar_client=edgar,
        exa_client=exa,
    )
    result = await run(ctx)

    not_found = _by_tag(result, SourceTag.NOT_FOUND)
    # Expect explicit NOT_FOUND for each of {revenue, headcount, growth}.
    assert len(not_found) >= 3, (
        f"Expected at least 3 NOT_FOUND claims (revenue/headcount/growth); "
        f"got {[c.to_dict() for c in result.claims]}"
    )
    joined = " ".join(c.text.lower() for c in not_found)
    assert "revenue" in joined
    assert "headcount" in joined
    assert "growth" in joined or "expansion" in joined or "funding" in joined

    # No ERROR claims when both clients return cleanly with no data.
    assert not _by_tag(result, SourceTag.ERROR)


# ---------------------------------------------------------------------------
# 4. Error path — Exa raises
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_error_path_exa_raises_returns_well_formed_result(caplog):
    edgar = _edgar_with(None)  # No EDGAR hit → revenue must fall back to Exa.
    exa = MagicMock()

    class _UpstreamBoom(RuntimeError):
        pass

    exa.search.side_effect = _UpstreamBoom("simulated 503 from Exa")

    ctx = AgentContext(
        account_name="Acme Logistics",
        edgar_client=edgar,
        exa_client=exa,
    )

    with caplog.at_level("ERROR"):
        result = await run(ctx)

    # Result must be a well-formed AgentResult.
    assert isinstance(result, AgentResult)
    assert result.agent_name == AGENT_NAME
    # Each claim still passes schema validation.
    [c.to_dict() for c in result.claims]

    # Either total error OR partial: failed categories tagged ERROR.
    errors = _by_tag(result, SourceTag.ERROR)
    assert errors, "Expected at least one ERROR claim when Exa raises"

    # Log hygiene: type name should appear, raw str(e) (which contains
    # the simulated body) must not.
    log_text = caplog.text
    assert "_UpstreamBoom" in log_text
    assert "simulated 503 from Exa" not in log_text


# ---------------------------------------------------------------------------
# 5. Schema enforcement — every claim round-trips to_dict cleanly.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Market-position + named-competitors extensions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_walmart_market_position_number_one_with_competitors():
    """Walmart-style: "largest US retailer" → PUBLIC #1 claim + competitors."""
    edgar = _edgar_with(None)
    exa = _exa_with(
        {
            "market share ranking": [
                {
                    "title": "Walmart remains the largest US retailer",
                    "url": "https://www.retaildive.com/news/walmart-largest-2025",
                    "snippet": (
                        "Walmart is the largest US retailer by revenue, "
                        "competitors include Amazon, Costco, and Target."
                    ),
                    "published_date": "2025-12-01",
                }
            ],
            "top competitors": [
                {
                    "title": "Walmart rivals in retail",
                    "url": "https://www.forbes.com/walmart-rivals",
                    "snippet": (
                        "Walmart competes with Amazon, Costco, Target, and Kroger "
                        "in the US retail sector."
                    ),
                    "published_date": "2025-11-15",
                }
            ],
        }
    )

    ctx = AgentContext(
        account_name="Walmart",
        edgar_client=edgar,
        exa_client=exa,
    )
    result = await run(ctx)

    public = _by_tag(result, SourceTag.PUBLIC)

    # Market-position PUBLIC claim with #1 framing.
    market_claims = [c for c in public if c.text.startswith("#1 in ")]
    assert market_claims, (
        f"Expected a #1 market-position PUBLIC claim; got "
        f"{[c.to_dict() for c in result.claims]}"
    )
    assert market_claims[0].source_url
    assert "retail" in market_claims[0].text.lower()

    # Competitors PUBLIC claim — names appear.
    competitor_claims = [
        c for c in public if "Named competitors in sector" in c.text
    ]
    assert competitor_claims, "Expected a named-competitors PUBLIC claim"
    text = competitor_claims[0].text
    assert "Amazon" in text
    # At least one of the other 3 names should make it through.
    assert any(name in text for name in ("Costco", "Target", "Kroger"))


@pytest.mark.asyncio
async def test_geodis_market_position_top_3_framing():
    """GEODIS-style: "top 3 global 3PL" → PUBLIC top-3 claim."""
    edgar = _edgar_with(None)
    exa = _exa_with(
        {
            "market share ranking": [
                {
                    "title": "GEODIS now top 3 global 3PL",
                    "url": "https://www.geodis.com/press/top-3-3pl-2025",
                    "snippet": (
                        "GEODIS is a top 3 global 3PL by revenue. "
                        "Competitors include DHL Supply Chain, Kuehne+Nagel, and DSV."
                    ),
                    "published_date": "2025-10-10",
                }
            ],
            "top competitors": [
                {
                    "title": "Top 3PL providers",
                    "url": "https://www.logisticsmgmt.com/top-3pl-2025",
                    "snippet": (
                        "GEODIS competes with DHL Supply Chain, Kuehne+Nagel, "
                        "DSV, and CEVA Logistics in the global 3PL market."
                    ),
                    "published_date": "2025-09-20",
                }
            ],
        }
    )

    ctx = AgentContext(
        account_name="GEODIS",
        edgar_client=edgar,
        exa_client=exa,
    )
    result = await run(ctx)

    public = _by_tag(result, SourceTag.PUBLIC)
    top3_claims = [c for c in public if c.text.startswith("top 3 in ")]
    assert top3_claims, (
        f"Expected a top-3 market-position PUBLIC claim; got "
        f"{[c.to_dict() for c in result.claims]}"
    )
    assert top3_claims[0].source_url
    # Sector label captured.
    assert "3pl" in top3_claims[0].text.lower() or "logistics" in top3_claims[0].text.lower()

    competitor_claims = [
        c for c in public if "Named competitors in sector" in c.text
    ]
    assert competitor_claims
    assert "DHL" in competitor_claims[0].text or "Kuehne" in competitor_claims[0].text


@pytest.mark.asyncio
async def test_notion_outside_icp_market_position_not_found_or_inferred():
    """Notion-style: no warehouse / sector signal → market position is
    NOT_FOUND or INFERRED, never silently omitted."""
    edgar = _edgar_with(None)
    exa = _exa_with({})  # All queries return [].

    ctx = AgentContext(
        account_name="Notion",
        edgar_client=edgar,
        exa_client=exa,
    )
    result = await run(ctx)

    # Either NOT_FOUND or INFERRED challenger framing — but never absent.
    candidates = [
        c
        for c in result.claims
        if (
            "market-position" in (c.text or "").lower()
            or "challenger" in (c.text or "").lower()
            or "no named competitors" in (c.text or "").lower()
            or "named competitors" in (c.text or "").lower()
        )
    ]
    assert candidates, (
        f"Expected at least one market-position / competitors claim; "
        f"got {[c.to_dict() for c in result.claims]}"
    )
    # All such claims must be NOT_FOUND or INFERRED (no silent PUBLIC fabrication).
    for c in candidates:
        assert c.source_tag in {SourceTag.NOT_FOUND, SourceTag.INFERRED}, (
            f"Unexpected source_tag for market-position claim: {c.to_dict()}"
        )

    # Specifically: at least one claim addresses each of {market position, competitors}.
    joined = " ".join(c.text.lower() for c in candidates)
    assert "market-position" in joined or "challenger" in joined
    assert "competitor" in joined


@pytest.mark.asyncio
async def test_market_position_query_raises_but_other_claims_still_emit(caplog):
    """Error path: one of the new Exa queries raises; existing
    revenue/headcount claims still emit (partial result); failed query
    contributes ERROR or NOT_FOUND."""
    edgar = _edgar_with(None)
    exa = MagicMock()

    class _BoomOnRanking(RuntimeError):
        pass

    def _search(query: str, **kwargs):
        q = query.lower()
        if "market share ranking" in q or "top competitors" in q:
            raise _BoomOnRanking("simulated outage on ranking query")
        if "revenue" in q or "sales" in q:
            return [
                {
                    "title": "Acme reports record revenue",
                    "url": "https://acme.example.com/news/revenue",
                    "snippet": "Acme reported revenue of $2.5 billion in 2025.",
                    "published_date": "2026-01-10",
                }
            ]
        if "employees" in q or "headcount" in q or "team of" in q:
            return [
                {
                    "title": "Acme team",
                    "url": "https://acme.example.com/about",
                    "snippet": "Acme has 8,500 employees globally.",
                    "published_date": "2026-01-12",
                }
            ]
        return []

    exa.search.side_effect = _search

    ctx = AgentContext(
        account_name="Acme Logistics",
        edgar_client=edgar,
        exa_client=exa,
    )
    with caplog.at_level("ERROR"):
        result = await run(ctx)

    # Result is well-formed; every claim round-trips.
    [c.to_dict() for c in result.claims]

    # Existing revenue PUBLIC claim still present.
    public = _by_tag(result, SourceTag.PUBLIC)
    assert any(
        "$2.5" in (c.text or "") or "2.5 billion" in (c.text or "").lower()
        for c in public
    ), "Expected revenue PUBLIC claim to survive the ranking-query failure"
    # Headcount PUBLIC claim still present.
    assert any(
        "8,500" in (c.text or "")
        for c in public
    ), "Expected headcount PUBLIC claim to survive the ranking-query failure"

    # The failed ranking queries should leave behind either an ERROR slot
    # or a NOT_FOUND slot for market-position / competitors.
    market_slots = [
        c
        for c in result.claims
        if (
            "market-position" in (c.text or "").lower()
            or "challenger" in (c.text or "").lower()
            or "in " in (c.text or "")[:8].lower()  # "#1 in" / "top 3 in"
        )
        or c.source_tag is SourceTag.ERROR
    ]
    assert market_slots, "Expected a market-position slot (ERROR or NOT_FOUND)"
    competitor_slots = [
        c for c in result.claims if "competitor" in (c.text or "").lower()
    ]
    assert competitor_slots, "Expected a competitors slot (ERROR or NOT_FOUND)"

    for c in market_slots + competitor_slots:
        assert c.source_tag in {
            SourceTag.ERROR,
            SourceTag.NOT_FOUND,
            SourceTag.INFERRED,
        }

    # Log hygiene — exception type name in logs, not the str(e) body.
    assert "_BoomOnRanking" in caplog.text
    assert "simulated outage on ranking query" not in caplog.text


@pytest.mark.asyncio
async def test_every_claim_validates_and_round_trips():
    edgar = _edgar_with(None)
    exa = _exa_with(
        {
            "revenue": [
                {
                    "title": "Acme Q4 results",
                    "url": "https://acme.example.com/news",
                    "snippet": "Acme reported revenue of $2.5 billion in 2025.",
                    "published_date": "2026-01-10",
                }
            ]
        }
    )

    ctx = AgentContext(
        account_name="Acme Logistics",
        edgar_client=edgar,
        exa_client=exa,
    )
    result = await run(ctx)

    for c in result.claims:
        # __post_init__ already ran; to_dict must produce a dict whose
        # source_tag round-trips through the enum.
        d = c.to_dict()
        assert "source_tag" in d
        assert d["source_tag"] in {t.value for t in SourceTag}
        assert d["text"]
