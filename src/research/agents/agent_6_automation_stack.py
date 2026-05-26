"""Agent 6 — Automation Stack (May 26 V1 spec §6).

Pulls WMS, LMS, ERP, robotics, and vision-deployment signals. The spec
notes "Job postings (best signal — tech mentioned in JDs)" — we scope
our Exa queries accordingly: one job-board-flavored query, one press /
case-study query.

Wraps existing infrastructure (Exa) into the new contract. The legacy
`findings_builder.py` `automation_vendors` topic remains until task #6
retires it.

Trust posture:
- Every URL passes `assert_safe_url` before storage.
- Logs use `type(e).__name__` only via `safe_log_exception`.
- Agent never raises; partial / total failure produces a graceful
  AgentResult instead.
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

AGENT_NAME = "agent_6_automation_stack"
SECTION_TITLE = "Automation Stack"

_MAX_PUBLIC_CLAIMS = 6
_EXA_RESULTS_PER_QUERY = 8


@dataclass
class AgentContext:
    account_name: str
    exa_client: Optional[ExaSearchClient] = None
    intent: Optional[str] = None


def _build_queries(company: str) -> List[str]:
    """Three scoped queries: job postings, vendor deployments, case studies."""
    return [
        (
            f"{company} job posting WMS Blue Yonder Manhattan SAP "
            f"warehouse management system inventory control engineer"
        ),
        (
            f"{company} Symbotic Locus Robotics AutoStore Berkshire Grey "
            f"warehouse robotics deployment automation vendor 2024 2025"
        ),
        (
            f"{company} case study automation success story robotics "
            f"vision AI warehouse"
        ),
    ]


def _claim_from_exa_hit(hit: dict) -> Optional[Claim]:
    url = (hit.get("url") or "").strip()
    title = (hit.get("title") or "").strip()
    snippet = (hit.get("snippet") or "").strip()
    if not url or not (title or snippet):
        return None
    try:
        assert_safe_url(url)
    except BlockedUrlError:
        return None

    text = snippet or title
    if len(text) > 360:
        text = text[:357] + "..."

    return Claim(
        text=text,
        source_tag=SourceTag.PUBLIC,
        source_url=url,
        date=(hit.get("published_date") or None),
    )


async def run(ctx: AgentContext) -> AgentResult:
    company = (ctx.account_name or "").strip()
    if not company:
        return AgentResult.error(
            AGENT_NAME, SECTION_TITLE, "Empty account_name supplied"
        )

    exa = ctx.exa_client
    if exa is None:
        if not settings.EXA_API_KEY:
            return AgentResult(
                agent_name=AGENT_NAME,
                section_title=SECTION_TITLE,
                claims=[
                    Claim(
                        text=(
                            "Exa API not configured; could not search "
                            "job postings, vendor deployments, or case studies."
                        ),
                        source_tag=SourceTag.NOT_FOUND,
                    )
                ],
            )
        exa = ExaSearchClient(settings.EXA_API_KEY)

    claims: List[Claim] = []
    failures = 0
    queries = _build_queries(company)
    for query in queries:
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

    if failures == len(queries):
        return AgentResult.error(
            AGENT_NAME, SECTION_TITLE, "All Exa searches failed."
        )

    if not claims:
        claims = [
            Claim(
                text=(
                    f"Searched job postings, vendor deployments, and "
                    f"case studies for {company}; no automation stack "
                    f"signals surfaced."
                ),
                source_tag=SourceTag.NOT_FOUND,
            )
        ]

    return AgentResult(
        agent_name=AGENT_NAME,
        section_title=SECTION_TITLE,
        claims=claims,
    )
