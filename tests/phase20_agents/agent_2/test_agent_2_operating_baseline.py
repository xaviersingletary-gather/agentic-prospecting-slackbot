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
