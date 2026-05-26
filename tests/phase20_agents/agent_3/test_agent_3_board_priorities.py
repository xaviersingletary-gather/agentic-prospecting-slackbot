"""Phase 20 — Agent 3 (Board Priorities) tests.

External Exa client is mocked. The agent must:
  - Emit PUBLIC claims for sourced quotes with a source_url.
  - Emit NOT_FOUND when both queries return empty.
  - Emit a single-ERROR result when every Exa query raises.
  - Skip individual results with unsafe URLs without crashing.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.research.agents.agent_3_board_priorities import AgentContext, run
from src.research.agents.contract import SourceTag


# Mark every async test in this module — conftest's auto-marker only picks
# up wrapped test functions, not bare `async def`.
pytestmark = pytest.mark.asyncio

def _hit(*, url, title="", snippet="", date=None):
    return {
        "url": url,
        "title": title,
        "snippet": snippet,
        "published_date": date,
    }


async def test_walmart_emits_public_claims_with_source_urls():
    fake_exa = MagicMock()
    fake_exa.search.return_value = [
        _hit(
            url="https://walmart.com/investors/2025-q1-earnings",
            title="Walmart Q1 2025 earnings call",
            snippet=(
                "CFO laid out three strategic priorities: automation in "
                "DCs, cost reduction, and shrink containment."
            ),
            date="2025-05-15",
        ),
        _hit(
            url="https://sec.gov/walmart/10-K/2025",
            title="Walmart 10-K 2025",
            snippet=(
                "Strategic initiatives include accelerated automation "
                "across our distribution network."
            ),
            date="2025-03-15",
        ),
    ]

    result = await run(
        AgentContext(account_name="Walmart", exa_client=fake_exa)
    )

    assert result.agent_name == "agent_3_board_priorities"
    public_claims = [c for c in result.claims if c.source_tag is SourceTag.PUBLIC]
    assert len(public_claims) >= 2
    for c in public_claims:
        assert c.source_url and c.source_url.startswith("https://")
    # Both queries were attempted.
    assert fake_exa.search.call_count == 2


async def test_geodis_private_returns_at_least_one_public_or_not_found():
    fake_exa = MagicMock()
    # Private 3PL — limited investor materials, but one news hit.
    fake_exa.search.side_effect = [
        [],  # first query: no investor materials
        [
            _hit(
                url="https://logisticsmgmt.com/geodis-board-2025",
                title="GEODIS board outlines automation focus",
                snippet="Board endorses automation investment for 2025-26.",
                date="2025-02-10",
            )
        ],
    ]

    result = await run(
        AgentContext(account_name="GEODIS", exa_client=fake_exa)
    )

    # Should have one PUBLIC claim from the second query.
    public_claims = [c for c in result.claims if c.source_tag is SourceTag.PUBLIC]
    assert len(public_claims) == 1
    assert "automation" in public_claims[0].text.lower()


async def test_notion_outside_icp_emits_not_found():
    fake_exa = MagicMock()
    fake_exa.search.return_value = []  # both queries empty

    result = await run(
        AgentContext(account_name="Notion", exa_client=fake_exa)
    )

    assert len(result.claims) == 1
    assert result.claims[0].source_tag is SourceTag.NOT_FOUND
    assert "Notion" in result.claims[0].text


async def test_exa_all_failures_returns_error_result(caplog):
    fake_exa = MagicMock()
    fake_exa.search.side_effect = RuntimeError("upstream down")

    with caplog.at_level("ERROR"):
        result = await run(
            AgentContext(account_name="Walmart", exa_client=fake_exa)
        )

    assert len(result.claims) == 1
    assert result.claims[0].source_tag is SourceTag.ERROR

    log_text = "\n".join(r.message for r in caplog.records)
    assert "RuntimeError" in log_text
    # str(e) must not leak — "upstream down" is the message.
    assert "upstream down" not in log_text


async def test_unsafe_url_results_are_skipped_silently():
    fake_exa = MagicMock()
    # First result has a private IP — must be dropped. Second is fine.
    fake_exa.search.return_value = [
        _hit(
            url="http://10.0.0.1/internal-doc",
            title="bad",
            snippet="should be dropped",
        ),
        _hit(
            url="https://walmart.com/safe",
            title="Walmart investor brief",
            snippet="Board priorities: automation rollout.",
        ),
    ]

    result = await run(
        AgentContext(account_name="Walmart", exa_client=fake_exa)
    )

    public_claims = [c for c in result.claims if c.source_tag is SourceTag.PUBLIC]
    assert len(public_claims) >= 1
    assert all("10.0.0.1" not in (c.source_url or "") for c in public_claims)


async def test_empty_account_name_returns_error():
    fake_exa = MagicMock()
    result = await run(AgentContext(account_name="", exa_client=fake_exa))
    assert len(result.claims) == 1
    assert result.claims[0].source_tag is SourceTag.ERROR
    fake_exa.search.assert_not_called()
