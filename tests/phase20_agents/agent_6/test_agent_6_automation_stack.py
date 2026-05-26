"""Phase 20 — Agent 6 (Automation Stack) tests.

External Exa client is mocked. Mirrors agent_3 coverage:
  - PUBLIC claims when sourceable
  - NOT_FOUND when all queries return empty
  - ERROR when every Exa query raises
  - Unsafe URLs silently dropped
  - Empty account name short-circuits with ERROR
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.research.agents.agent_6_automation_stack import AgentContext, run
from src.research.agents.contract import SourceTag


pytestmark = pytest.mark.asyncio


def _hit(*, url, title="", snippet="", date=None):
    return {
        "url": url,
        "title": title,
        "snippet": snippet,
        "published_date": date,
    }


async def test_walmart_emits_public_claims_for_job_postings_and_vendors():
    fake_exa = MagicMock()
    fake_exa.search.side_effect = [
        [
            _hit(
                url="https://walmart.com/careers/wms-engineer-bentonville",
                title="WMS Engineer — Walmart Distribution",
                snippet=(
                    "Manage Blue Yonder WMS deployments across the "
                    "Walmart distribution network."
                ),
            )
        ],
        [
            _hit(
                url="https://logistics.com/walmart-symbotic-rollout",
                title="Walmart accelerates Symbotic robotics rollout",
                snippet="Walmart expands Symbotic deployment to 25 DCs.",
                date="2025-04-10",
            )
        ],
        [],
    ]

    result = await run(
        AgentContext(account_name="Walmart", exa_client=fake_exa)
    )

    public_claims = [c for c in result.claims if c.source_tag is SourceTag.PUBLIC]
    assert len(public_claims) >= 2
    assert any("Blue Yonder" in c.text or "WMS" in c.text for c in public_claims)
    assert any("Symbotic" in c.text for c in public_claims)
    assert fake_exa.search.call_count == 3


async def test_geodis_private_has_partial_signal():
    fake_exa = MagicMock()
    fake_exa.search.side_effect = [
        [
            _hit(
                url="https://geodis.com/careers/automation-lead",
                title="Automation Lead — GEODIS",
                snippet="Lead automation initiatives across GEODIS facilities.",
            )
        ],
        [],
        [],
    ]
    result = await run(
        AgentContext(account_name="GEODIS", exa_client=fake_exa)
    )
    public = [c for c in result.claims if c.source_tag is SourceTag.PUBLIC]
    assert len(public) == 1


async def test_notion_outside_icp_emits_not_found():
    fake_exa = MagicMock()
    fake_exa.search.return_value = []

    result = await run(
        AgentContext(account_name="Notion", exa_client=fake_exa)
    )
    assert len(result.claims) == 1
    assert result.claims[0].source_tag is SourceTag.NOT_FOUND


async def test_total_exa_failure_returns_error(caplog):
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
    assert "upstream down" not in log_text


async def test_unsafe_url_dropped_silently():
    fake_exa = MagicMock()
    fake_exa.search.return_value = [
        _hit(url="http://10.0.0.1/internal", title="bad", snippet="x"),
        _hit(
            url="https://walmart.com/careers/wms",
            title="WMS Engineer",
            snippet="Blue Yonder WMS owner",
        ),
    ]
    result = await run(
        AgentContext(account_name="Walmart", exa_client=fake_exa)
    )
    public = [c for c in result.claims if c.source_tag is SourceTag.PUBLIC]
    assert all("10.0.0.1" not in (c.source_url or "") for c in public)


async def test_empty_account_name_returns_error():
    fake_exa = MagicMock()
    result = await run(AgentContext(account_name="", exa_client=fake_exa))
    assert len(result.claims) == 1
    assert result.claims[0].source_tag is SourceTag.ERROR
    fake_exa.search.assert_not_called()
