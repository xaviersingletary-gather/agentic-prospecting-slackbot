"""Phase 20 — Agent 4 (Customer-Facing Signals) tests.

Mocks the Exa client. No real network calls. Covers:
  - Walmart (public retail) → multiple PUBLIC claims with distinct URLs.
  - GEODIS (private 3PL) → at least one PUBLIC claim from a Reddit /
    Glassdoor-style mix.
  - Notion (outside-ICP control) → NOT_FOUND claim because nothing in
    the snippets matches fulfillment / inventory / chargeback keywords.
  - Error path → every Exa search raises; agent returns a single
    ERROR-claim AgentResult; log line uses type(e).__name__ via
    safe_log_exception (asserted indirectly via caplog).
  - Schema enforcement → every emitted Claim round-trips through
    to_dict() without raising.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import pytest

from src.research.agents.agent_4_customer_signals import (
    AGENT_NAME,
    SECTION_TITLE,
    AgentContext,
    run,
)
from src.research.agents.contract import AgentResult, Claim, SourceTag


# ---------------------------------------------------------------------------
# Fake Exa client
# ---------------------------------------------------------------------------


class FakeExa:
    """Tiny stand-in for ExaSearchClient that dispatches by query slot.

    Attach a `queries` dict that maps a substring (matched against the
    query string) to either:
      - a list of result dicts (returned directly), or
      - an Exception instance (raised when that query runs).
    Default: empty list.
    """

    def __init__(self, queries: Optional[Dict[str, Any]] = None):
        self.queries: Dict[str, Any] = queries or {}
        self.calls: List[str] = []

    def search(
        self,
        query: str,
        *,
        num_results: int = 10,
        include_domain: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        self.calls.append(query)
        for needle, payload in self.queries.items():
            if needle in query:
                if isinstance(payload, Exception):
                    raise payload
                return payload
        return []


class AlwaysRaisingExa:
    """Exa stub that raises on every search call."""

    def __init__(self, exc: Exception):
        self.exc = exc
        self.calls = 0

    def search(self, query: str, **_kw: Any) -> List[Dict[str, Any]]:
        self.calls += 1
        raise self.exc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _assert_schema(result: AgentResult) -> None:
    """Every claim must round-trip to_dict()."""
    assert result.agent_name == AGENT_NAME
    assert result.section_title == SECTION_TITLE
    assert result.claims, "claims must never be empty"
    for c in result.claims:
        d = c.to_dict()
        assert d["text"]
        assert "source_tag" in d


# ---------------------------------------------------------------------------
# Walmart — public retail, multiple PUBLIC claims
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_walmart_emits_multiple_public_claims():
    exa = FakeExa(
        queries={
            "chargeback fulfillment complaint retailer scorecard": [
                {
                    "title": "Walmart OTIF chargeback fines hit suppliers",
                    "url": "https://www.supplychaindive.com/news/walmart-otif-chargeback-fines/2025",
                    "snippet": (
                        "Walmart tightened its OTIF scorecard, hitting "
                        "suppliers with fulfillment chargebacks for missed "
                        "delivery windows."
                    ),
                    "published_date": "2025-03-14",
                },
                {
                    "title": "Suppliers protest Walmart fulfillment penalties",
                    "url": "https://www.retaildive.com/news/walmart-fulfillment-chargebacks-2025/",
                    "snippet": (
                        "Smaller vendors say chargeback math on inventory "
                        "accuracy issues is opaque."
                    ),
                    "published_date": "2025-04-02",
                },
            ],
            "Glassdoor warehouse review complaint": [
                {
                    "title": "Walmart DC associates complain about inventory accuracy",
                    "url": "https://example.com/news/walmart-dc-glassdoor-summary-2025",
                    "snippet": (
                        "Warehouse associates flagged inventory accuracy "
                        "problems and shipping delays in reviews."
                    ),
                    "published_date": "2025-01-22",
                },
            ],
            "Reddit fulfillment shipping accuracy complaint": [
                {
                    # No relevant keyword — should NOT promote to PUBLIC.
                    "title": "Walmart stock split rumor",
                    "url": "https://example.com/news/walmart-stock-split",
                    "snippet": "Investors speculate on a 2025 stock split.",
                    "published_date": "2025-02-10",
                },
            ],
            "customer service issue inventory accuracy news": [
                {
                    "title": "Walmart customer service spike on missing items",
                    "url": "https://www.consumerreports.org/walmart-missing-items-2025",
                    "snippet": (
                        "Customer service complaints about missing items "
                        "and wrong-item shipments rose in Q1."
                    ),
                    "published_date": "2025-04-19",
                },
            ],
        }
    )

    ctx = AgentContext(account_name="Walmart", exa_client=exa)
    result = await run(ctx)

    _assert_schema(result)
    public = [c for c in result.claims if c.source_tag is SourceTag.PUBLIC]
    assert len(public) >= 2, f"expected >=2 PUBLIC claims, got {public}"
    urls = {c.source_url for c in public}
    assert len(urls) == len(public), "PUBLIC claims must have distinct URLs"
    # Non-relevant snippet (stock split) was correctly filtered out.
    assert not any(
        "stock-split" in (c.source_url or "") for c in public
    )
    # All 4 queries should have run.
    assert len(exa.calls) == 4


# ---------------------------------------------------------------------------
# GEODIS — private 3PL, Reddit + Glassdoor mix
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_geodis_emits_at_least_one_public_claim_from_reddit_glassdoor_mix():
    exa = FakeExa(
        queries={
            "chargeback fulfillment complaint retailer scorecard": [],
            "Glassdoor warehouse review complaint": [
                {
                    "title": "GEODIS warehouse Glassdoor — fulfillment pressure",
                    "url": "https://example.com/glassdoor-mirror/geodis-warehouse-2024",
                    "snippet": (
                        "Associates describe fulfillment pressure and "
                        "inventory accuracy targets under peak season."
                    ),
                    "published_date": "2024-11-08",
                },
            ],
            "Reddit fulfillment shipping accuracy complaint": [
                {
                    "title": "r/logistics thread on GEODIS shipping accuracy",
                    "url": "https://example.com/reddit-archive/geodis-shipping-2025",
                    "snippet": (
                        "Multiple posters report shipping accuracy and "
                        "delayed shipment issues with GEODIS contract sites."
                    ),
                    "published_date": "2025-02-01",
                },
            ],
            "customer service issue inventory accuracy news": [],
        }
    )

    ctx = AgentContext(account_name="GEODIS", exa_client=exa)
    result = await run(ctx)

    _assert_schema(result)
    public = [c for c in result.claims if c.source_tag is SourceTag.PUBLIC]
    assert len(public) >= 1, f"expected >=1 PUBLIC claim, got {public}"


# ---------------------------------------------------------------------------
# Notion — outside-ICP control, no warehouse / fulfillment content
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notion_outside_icp_returns_not_found():
    exa = FakeExa(
        queries={
            "chargeback fulfillment complaint retailer scorecard": [
                {
                    "title": "Notion launches AI features",
                    "url": "https://example.com/news/notion-ai-2025",
                    "snippet": "Notion shipped AI blocks for the workspace.",
                    "published_date": "2025-05-01",
                },
            ],
            "Glassdoor warehouse review complaint": [
                {
                    "title": "Notion engineering culture",
                    "url": "https://example.com/blog/notion-eng-culture",
                    "snippet": "Engineering interviews discuss product velocity.",
                    "published_date": "2025-03-14",
                },
            ],
            "Reddit fulfillment shipping accuracy complaint": [
                {
                    "title": "Notion vs Obsidian thread",
                    "url": "https://example.com/threads/notion-vs-obsidian",
                    "snippet": "Users compare PKM tools and database features.",
                    "published_date": "2025-02-22",
                },
            ],
            "customer service issue inventory accuracy news": [],
        }
    )

    ctx = AgentContext(account_name="Notion", exa_client=exa)
    result = await run(ctx)

    _assert_schema(result)
    tags = [c.source_tag for c in result.claims]
    assert SourceTag.NOT_FOUND in tags, (
        f"expected NOT_FOUND claim for outside-ICP control, got {tags}"
    )
    # And no PUBLIC claims, since none of the snippets match the
    # warehouse-relevant keyword filter.
    assert not any(c.source_tag is SourceTag.PUBLIC for c in result.claims)


# ---------------------------------------------------------------------------
# Error path — every Exa search raises
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_queries_failing_returns_single_error_claim(caplog):
    exa = AlwaysRaisingExa(RuntimeError("exa exploded with secret token"))

    ctx = AgentContext(account_name="AcmeWarehouseCo", exa_client=exa)

    with caplog.at_level(logging.ERROR):
        result = await run(ctx)

    _assert_schema(result)
    assert exa.calls == 4, "all 4 queries should have been attempted"
    assert len(result.claims) == 1
    assert result.claims[0].source_tag is SourceTag.ERROR
    # No leaked exception string anywhere in logs.
    joined_logs = "\n".join(r.getMessage() for r in caplog.records)
    assert "secret token" not in joined_logs
    # The safe logger formats with type name in brackets.
    assert "[RuntimeError]" in joined_logs


# ---------------------------------------------------------------------------
# Partial failure — one query raises, others succeed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_partial_failure_keeps_going_and_notes_it(caplog):
    exa = FakeExa(
        queries={
            "chargeback fulfillment complaint retailer scorecard":
                RuntimeError("temporary exa failure"),
            "Glassdoor warehouse review complaint": [
                {
                    "title": "Warehouse review thread",
                    "url": "https://example.com/glassdoor/acme-2025",
                    "snippet": (
                        "Reviewers cite inventory accuracy and shipping "
                        "delays at peak."
                    ),
                    "published_date": "2025-03-01",
                },
            ],
            "Reddit fulfillment shipping accuracy complaint": [],
            "customer service issue inventory accuracy news": [],
        }
    )

    ctx = AgentContext(account_name="Acme Logistics", exa_client=exa)
    with caplog.at_level(logging.ERROR):
        result = await run(ctx)

    _assert_schema(result)
    assert result.notes is not None and "Partial" in result.notes
    public = [c for c in result.claims if c.source_tag is SourceTag.PUBLIC]
    assert len(public) == 1
    joined_logs = "\n".join(r.getMessage() for r in caplog.records)
    assert "[RuntimeError]" in joined_logs
    assert "temporary exa failure" not in joined_logs


# ---------------------------------------------------------------------------
# SSRF guard — blocked URLs are dropped, not crashed on
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_blocked_url_dropped_silently():
    exa = FakeExa(
        queries={
            "chargeback fulfillment complaint retailer scorecard": [
                {
                    # localhost — should fail assert_safe_url.
                    "title": "Local doc on chargebacks",
                    "url": "http://localhost/secret-internal",
                    "snippet": "Inventory chargeback details internal",
                    "published_date": "2025-01-01",
                },
                {
                    "title": "Public chargeback news",
                    "url": "https://example.com/news/chargeback-public",
                    "snippet": "Chargeback fulfillment penalties rose.",
                    "published_date": "2025-01-02",
                },
            ],
        }
    )
    ctx = AgentContext(account_name="Acme", exa_client=exa)
    result = await run(ctx)

    _assert_schema(result)
    public = [c for c in result.claims if c.source_tag is SourceTag.PUBLIC]
    assert public, "should have promoted the safe URL"
    for c in public:
        assert "localhost" not in (c.source_url or "")


# ---------------------------------------------------------------------------
# Empty account_name → ERROR
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_account_name_returns_error():
    exa = FakeExa()
    ctx = AgentContext(account_name="   ", exa_client=exa)
    result = await run(ctx)
    assert result.claims[0].source_tag is SourceTag.ERROR
    # No Exa calls should have happened.
    assert exa.calls == []


# ---------------------------------------------------------------------------
# Missing exa_client → graceful NOT_FOUND
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_exa_client_emits_not_found():
    ctx = AgentContext(account_name="Walmart", exa_client=None)
    result = await run(ctx)
    _assert_schema(result)
    assert result.claims[0].source_tag is SourceTag.NOT_FOUND
