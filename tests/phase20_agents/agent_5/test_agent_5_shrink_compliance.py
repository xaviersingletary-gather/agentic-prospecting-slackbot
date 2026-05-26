"""Phase 20 — Agent 5 (Shrink & Compliance) tests.

Mocks EDGAR + Exa at the agent module's import path. No real network.

Required coverage (per docs/agent-contract.md §Testing):
- Walmart (public retail): EDGAR returns 10-K with risk-section quote;
  Exa returns news. Assert PUBLIC claim with 10-K source_url AND date.
- GEODIS (private 3PL): EDGAR returns None; Exa returns one regulatory
  news snippet. Assert PUBLIC claim. 3PL has no FDA jurisdiction → no
  FDA INFERRED claim emitted.
- Notion (outside-ICP SaaS): EDGAR and Exa both empty → NOT_FOUND claim.
- Error path: EDGAR raises → Exa branch still runs, no re-raise, log
  carries type(e).__name__ (verified via safe_log_exception path).
"""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import MagicMock, patch

import pytest

from src.research.agents.agent_5_shrink_compliance import (
    AGENT_NAME,
    SECTION_TITLE,
    AgentContext,
    run,
)
from src.research.agents.contract import AgentResult, Claim, SourceTag

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

WALMART_10K_URL = "https://www.sec.gov/Archives/edgar/data/104169/000010416925000012/wmt-20250131.htm"

WALMART_RISK_HTML = """
<html><body>
<h1>Item 1A. Risk Factors</h1>
<p>Our business is subject to inventory shrinkage from theft, damage, and
administrative errors. Inventory shrinkage in fiscal 2025 negatively
impacted gross profit. We also face periodic audit and regulatory
review across multiple jurisdictions, including FDA oversight of our
pharmacy and food businesses, which could result in recall or warning
letter actions.</p>
<h2>Item 1B. Other</h2>
<p>Unrelated text that should not be quoted.</p>
</body></html>
"""

WALMART_EDGAR_RESULT = {
    "entity_name": "WALMART INC",
    "file_date": "2025-03-21",
    "period": "2025-01-31",
    "cik": 104169,
    "accession_no": "0000104169-25-000012",
    "document_url": WALMART_10K_URL,
    "form_type": "10-K",
}

WALMART_EXA_HITS = [
    {
        "title": "Walmart pharmacy audit settlement",
        "url": "https://www.reuters.com/business/walmart-fda-audit-2025/",
        "snippet": (
            "Walmart agreed to a $50M settlement following a multi-state "
            "audit of its pharmacy inventory controls in 2025."
        ),
        "published_date": "2025-09-12",
    },
]

GEODIS_EXA_HITS = [
    {
        "title": "GEODIS regulatory penalty for customs filing error",
        "url": "https://www.supplychaindive.com/news/geodis-customs-penalty-2025/",
        "snippet": (
            "Logistics provider GEODIS faced a regulatory penalty in 2025 "
            "after a customs filing audit identified clearance compliance "
            "gaps at two North American distribution sites."
        ),
        "published_date": "2025-06-04",
    },
]


def _run(ctx: AgentContext) -> AgentResult:
    return asyncio.get_event_loop().run_until_complete(run(ctx))


# Use a fresh event loop per test to avoid asyncio cross-test contamination
@pytest.fixture(autouse=True)
def _fresh_event_loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        yield loop
    finally:
        loop.close()
        asyncio.set_event_loop(asyncio.new_event_loop())


# ---------------------------------------------------------------------------
# Walmart — public co with 10-K risk-section hit + Exa news
# ---------------------------------------------------------------------------


@patch("src.research.agents.agent_5_shrink_compliance.fetch_html")
@patch("src.research.agents.agent_5_shrink_compliance.EdgarClient")
def test_walmart_emits_10k_public_claim_with_url_and_date(
    mock_edgar_cls, mock_fetch_html
):
    mock_edgar = MagicMock()
    mock_edgar.find_latest_10k.return_value = WALMART_EDGAR_RESULT
    mock_edgar_cls.return_value = mock_edgar
    mock_fetch_html.return_value = WALMART_RISK_HTML

    exa = MagicMock()
    exa.search.return_value = WALMART_EXA_HITS

    result = _run(
        AgentContext(
            account_name="Walmart",
            industry="Retail",
            exa_client=exa,
        )
    )

    # Schema: result round-trips
    assert result.agent_name == AGENT_NAME
    assert result.section_title == SECTION_TITLE
    assert result.duration_ms is not None
    serialized = result.to_dict()
    assert serialized["agent_name"] == AGENT_NAME

    # At least one PUBLIC claim whose source_url is the 10-K AND has a date.
    public_10k = [
        c for c in result.claims
        if c.source_tag is SourceTag.PUBLIC
        and c.source_url == WALMART_10K_URL
    ]
    assert public_10k, "Expected at least one PUBLIC claim with 10-K source_url"
    assert any(c.date for c in public_10k), (
        "Expected at least one 10-K claim to carry a date (filing period)"
    )

    # The 10-K quote text should be brief — never the full risk section.
    for c in public_10k:
        assert len(c.text) <= 400

    # Exa news claim is present too (Reuters)
    assert any(
        c.source_tag is SourceTag.PUBLIC
        and "reuters.com" in (c.source_url or "")
        for c in result.claims
    )


# ---------------------------------------------------------------------------
# GEODIS — private 3PL, no SEC filing, Exa returns one news hit
# ---------------------------------------------------------------------------


@patch("src.research.agents.agent_5_shrink_compliance.fetch_html")
@patch("src.research.agents.agent_5_shrink_compliance.EdgarClient")
def test_geodis_private_3pl_no_fda_inference(mock_edgar_cls, mock_fetch_html):
    mock_edgar = MagicMock()
    mock_edgar.find_latest_10k.return_value = None  # no SEC filing
    mock_edgar_cls.return_value = mock_edgar

    exa = MagicMock()
    exa.search.return_value = GEODIS_EXA_HITS

    result = _run(
        AgentContext(
            account_name="GEODIS",
            industry="3PL / Logistics",
            exa_client=exa,
        )
    )

    # fetch_html must not have been called — no document_url to fetch.
    mock_fetch_html.assert_not_called()

    # At least one PUBLIC claim from Exa
    public_claims = [c for c in result.claims if c.source_tag is SourceTag.PUBLIC]
    assert public_claims, "Expected at least one PUBLIC claim from Exa"
    assert any(
        "supplychaindive.com" in (c.source_url or "") for c in public_claims
    )

    # 3PL is NOT an FDA-jurisdiction industry → no INFERRED FDA claim.
    inferred = [c for c in result.claims if c.source_tag is SourceTag.INFERRED]
    assert not inferred, (
        f"3PL should not emit FDA INFERRED claim, got: {[c.text for c in inferred]}"
    )


# ---------------------------------------------------------------------------
# Notion — outside-ICP SaaS, no findings anywhere
# ---------------------------------------------------------------------------


@patch("src.research.agents.agent_5_shrink_compliance.fetch_html")
@patch("src.research.agents.agent_5_shrink_compliance.EdgarClient")
def test_notion_no_findings_emits_not_found(mock_edgar_cls, mock_fetch_html):
    mock_edgar = MagicMock()
    mock_edgar.find_latest_10k.return_value = None
    mock_edgar_cls.return_value = mock_edgar

    exa = MagicMock()
    exa.search.return_value = []  # nothing for any query

    result = _run(
        AgentContext(
            account_name="Notion",
            industry="SaaS",
            exa_client=exa,
        )
    )

    # Exactly one NOT_FOUND claim, no PUBLIC / INFERRED noise.
    assert len(result.claims) == 1
    only = result.claims[0]
    assert only.source_tag is SourceTag.NOT_FOUND
    assert "searched" in only.text.lower() or "no sourced" in only.text.lower()
    # All three Exa queries were attempted.
    assert exa.search.call_count == 3


# ---------------------------------------------------------------------------
# Error path — EDGAR raises; Exa branch still runs; no re-raise; safe log.
# ---------------------------------------------------------------------------


@patch("src.research.agents.agent_5_shrink_compliance.fetch_html")
@patch("src.research.agents.agent_5_shrink_compliance.EdgarClient")
def test_edgar_raises_does_not_crash_and_logs_type_name(
    mock_edgar_cls, mock_fetch_html, caplog
):
    class _BoomError(RuntimeError):
        pass

    mock_edgar = MagicMock()
    mock_edgar.find_latest_10k.side_effect = _BoomError("token=abcd1234 leaked")
    mock_edgar_cls.return_value = mock_edgar

    exa = MagicMock()
    exa.search.return_value = GEODIS_EXA_HITS  # Exa still works

    caplog.set_level(logging.ERROR, logger="src.research.agents.agent_5_shrink_compliance")

    # Must not raise
    result = _run(
        AgentContext(
            account_name="Walmart",
            industry="Retail",
            exa_client=exa,
        )
    )

    # Exa branch still ran — we should have a PUBLIC claim from the hit.
    assert any(c.source_tag is SourceTag.PUBLIC for c in result.claims)
    # fetch_html should NOT have been called — no document_url available.
    mock_fetch_html.assert_not_called()

    # Log must carry type(e).__name__ — and never the str(e) payload.
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("_BoomError" in r.getMessage() for r in error_records), (
        f"Expected '_BoomError' in error log, got: "
        f"{[r.getMessage() for r in error_records]}"
    )
    for r in error_records:
        assert "abcd1234" not in r.getMessage(), (
            "Exception str() payload leaked into log"
        )
        assert "token=" not in r.getMessage()


# ---------------------------------------------------------------------------
# Bonus: schema enforcement — every claim survives to_dict()
# ---------------------------------------------------------------------------


@patch("src.research.agents.agent_5_shrink_compliance.fetch_html")
@patch("src.research.agents.agent_5_shrink_compliance.EdgarClient")
def test_every_claim_round_trips_to_dict(mock_edgar_cls, mock_fetch_html):
    mock_edgar = MagicMock()
    mock_edgar.find_latest_10k.return_value = WALMART_EDGAR_RESULT
    mock_edgar_cls.return_value = mock_edgar
    mock_fetch_html.return_value = WALMART_RISK_HTML

    exa = MagicMock()
    exa.search.return_value = WALMART_EXA_HITS

    result = _run(
        AgentContext(account_name="Walmart", industry="Retail", exa_client=exa)
    )
    payload = result.to_dict()
    for claim_dict in payload["claims"]:
        assert "text" in claim_dict
        assert "source_tag" in claim_dict
        assert claim_dict["source_tag"] in {
            "public", "inferred", "internal", "not_found", "error"
        }


# ---------------------------------------------------------------------------
# Bonus: FDA-industry INFERRED claim *is* emitted when no public evidence
# exists for a pharma / F&B account. Verifies the inverse of the GEODIS test.
# ---------------------------------------------------------------------------


@patch("src.research.agents.agent_5_shrink_compliance.fetch_html")
@patch("src.research.agents.agent_5_shrink_compliance.EdgarClient")
def test_pharma_with_no_evidence_emits_fda_inferred(
    mock_edgar_cls, mock_fetch_html
):
    mock_edgar = MagicMock()
    mock_edgar.find_latest_10k.return_value = None
    mock_edgar_cls.return_value = mock_edgar

    exa = MagicMock()
    exa.search.return_value = []

    result = _run(
        AgentContext(
            account_name="MidMarket Pharma Co",
            industry="Pharmaceutical",
            exa_client=exa,
        )
    )

    inferred = [c for c in result.claims if c.source_tag is SourceTag.INFERRED]
    assert inferred, "Pharma + no evidence should emit INFERRED FDA claim"
    assert inferred[0].inference_logic
    assert "fda" in inferred[0].inference_logic.lower()
