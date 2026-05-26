"""Agent 3 — Board Priorities (May 26 V1 spec §6).

Pulls investor-day commentary, earnings-call quotes, 10-K risk-section
signals, and executive remarks. Emits one PUBLIC `Claim` per surfaced
quote with the source URL and (when available) publication date.

This agent wraps existing infrastructure (Exa + EDGAR) into the new
contract — it does NOT call the multi-topic LLM extractor in
`findings_builder.py`. That extractor is being retired by the new
8-agent dispatcher in task #6.

Trust posture mirrors `findings_builder`:
- Exa results pass `assert_safe_url` before the URL is stored on a Claim.
- Every external call is wrapped in a narrow try/except. The agent
  never raises; it returns either a partial result (one branch worked)
  or `AgentResult.error(...)` (both branches failed).
- Logs use `type(e).__name__` only (CLAUDE.md log hygiene).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

from src.config import settings
from src.integrations.exa.client import ExaSearchClient
from src.research.agents.contract import AgentResult, Claim, SourceTag
from src.security.exception_logger import safe_log_exception
from src.security.url_guard import BlockedUrlError, assert_safe_url

logger = logging.getLogger(__name__)

AGENT_NAME = "agent_3_board_priorities"
SECTION_TITLE = "Board Priorities"

# Cap claims per source so a single Exa pass can't flood the section.
_MAX_PUBLIC_CLAIMS = 6
_EXA_RESULTS_PER_QUERY = 8


@dataclass
class AgentContext:
    """Minimal context — Exa client injected so tests can mock it."""

    account_name: str
    exa_client: Optional[ExaSearchClient] = None
    intent: Optional[str] = None


def _build_queries(company: str) -> List[str]:
    """Two scoped Exa queries: investor materials + executive remarks."""
    return [
        (
            f"{company} earnings call investor day strategic priorities "
            f"cost reduction automation supply chain CEO CFO 2024 2025 2026"
        ),
        (
            f"{company} 10-K risk factors strategic initiatives capital "
            f"allocation board priorities annual report"
        ),
    ]


def _claim_from_exa_hit(hit: dict) -> Optional[Claim]:
    """Coerce one Exa result into a PUBLIC Claim, or None to skip."""
    url = (hit.get("url") or "").strip()
    title = (hit.get("title") or "").strip()
    snippet = (hit.get("snippet") or "").strip()
    if not url or not (title or snippet):
        return None
    try:
        assert_safe_url(url)
    except BlockedUrlError:
        return None

    # Prefer the snippet (richer signal) but fall back to the title.
    text = snippet or title
    # Truncate so a runaway snippet doesn't dominate the Slack section.
    if len(text) > 360:
        text = text[:357] + "..."

    return Claim(
        text=text,
        source_tag=SourceTag.PUBLIC,
        source_url=url,
        date=(hit.get("published_date") or None),
    )


async def run(ctx: AgentContext) -> AgentResult:
    """Entry point the dispatcher calls."""
    company = (ctx.account_name or "").strip()
    if not company:
        return AgentResult.error(
            AGENT_NAME, SECTION_TITLE, "Empty account_name supplied"
        )

    exa = ctx.exa_client
    if exa is None:
        # Lazy default — production path. Tests always pass their own
        # mocked client so this branch isn't exercised.
        if not settings.EXA_API_KEY:
            return AgentResult(
                agent_name=AGENT_NAME,
                section_title=SECTION_TITLE,
                claims=[
                    Claim(
                        text=(
                            "Exa API not configured; could not search "
                            "investor materials or executive remarks."
                        ),
                        source_tag=SourceTag.NOT_FOUND,
                    )
                ],
            )
        exa = ExaSearchClient(settings.EXA_API_KEY)

    claims: List[Claim] = []
    failures = 0
    for query in _build_queries(company):
        try:
            hits = exa.search(query, num_results=_EXA_RESULTS_PER_QUERY)
        except Exception as e:  # noqa: BLE001
            safe_log_exception(logger, e, f"{AGENT_NAME} exa.search failed")
            failures += 1
            continue
        for hit in hits or []:
            claim = _claim_from_exa_hit(hit)
            if claim is not None:
                claims.append(claim)
            if len(claims) >= _MAX_PUBLIC_CLAIMS:
                break
        if len(claims) >= _MAX_PUBLIC_CLAIMS:
            break

    # Both Exa branches failed → total agent failure (rendered as ERROR).
    if failures == len(_build_queries(company)):
        return AgentResult.error(
            AGENT_NAME, SECTION_TITLE, "All Exa searches failed."
        )

    if not claims:
        claims = [
            Claim(
                text=(
                    f"Searched investor materials and executive remarks "
                    f"for {company}; no public board priorities surfaced."
                ),
                source_tag=SourceTag.NOT_FOUND,
            )
        ]

    return AgentResult(
        agent_name=AGENT_NAME,
        section_title=SECTION_TITLE,
        claims=claims,
    )
