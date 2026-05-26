"""Agent 4 — Customer-Facing Signals (May 26 spec §6).

Pulls public chatter on retailer chargebacks, fulfillment complaints,
customer-service issues, and warehouse-employee sentiment that hint at
inventory-accuracy pain. Sources: news + Reddit + Glassdoor + retailer
scorecards as surfaced by Exa.

Trust boundary: every Exa snippet is treated as untrusted user input.
We never feed snippets into a tool-calling LLM here — only into the
deterministic claim builder below. Every `source_url` runs through
`assert_safe_url` before being stored on a Claim. Per CLAUDE.md log
hygiene, exception logging goes through `safe_log_exception` and never
echoes `str(e)`.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from src.integrations.exa.client import ExaSearchClient
from src.research.agents.contract import (
    AgentResult,
    Claim,
    SourceTag,
)
from src.security.exception_logger import safe_log_exception
from src.security.url_guard import BlockedUrlError, assert_safe_url

logger = logging.getLogger(__name__)

AGENT_NAME = "agent_4_customer_signals"
SECTION_TITLE = "Customer-Facing Signals"

# Keep the queries narrow and named — easier to debug, easier to surface
# in the NOT_FOUND text below when all four come back empty.
_QUERY_TEMPLATES: List[Tuple[str, str]] = [
    (
        "retailer_chargebacks",
        "{company} chargeback fulfillment complaint retailer scorecard 2024 2025 2026",
    ),
    (
        "glassdoor_warehouse",
        "{company} Glassdoor warehouse review complaint",
    ),
    (
        "reddit_fulfillment",
        "{company} Reddit fulfillment shipping accuracy complaint",
    ),
    (
        "customer_service_news",
        "{company} customer service issue inventory accuracy news",
    ),
]

# Lightweight keyword filter on snippets. We only want to elevate a
# snippet to PUBLIC when it talks about something operationally relevant
# (fulfillment / inventory / chargeback / shipping / accuracy). This
# keeps generic corporate-news noise out of the section without trying
# to do semantic classification.
_SIGNAL_KEYWORDS: Tuple[str, ...] = (
    "chargeback",
    "charge-back",
    "fulfillment",
    "fulfilment",
    "shipping",
    "shipment",
    "inventory",
    "accuracy",
    "stockout",
    "out of stock",
    "complaint",
    "complaints",
    "scorecard",
    "warehouse",
    "OTIF",
    "on-time",
    "on time",
    "delayed",
    "delay",
    "missing",
    "wrong item",
    "service issue",
    "customer service",
    "NPS",
    "sentiment",
    "audit",
    "review",
    "reviews",
)

# Per-claim cap so one query can't drown the section.
_MAX_CLAIMS_PER_QUERY = 2
# Whole-section cap for safety.
_MAX_TOTAL_CLAIMS = 6


@dataclass
class AgentContext:
    """Minimal context Agent 4 needs.

    Only `account_name` is required. `account_domain` is unused right
    now — Exa queries scope by company name; we keep the field so the
    dispatcher signature stays uniform across agents.
    `exa_client` is injectable so tests can patch a fake without
    monkeypatching the module-level import.
    """

    account_name: str
    account_domain: Optional[str] = None
    intent: Optional[str] = None
    exa_client: Optional[ExaSearchClient] = None


def _has_signal_keyword(text: str) -> bool:
    if not text:
        return False
    haystack = text.lower()
    return any(kw.lower() in haystack for kw in _SIGNAL_KEYWORDS)


def _truncate(text: str, limit: int = 240) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _claim_text_from_result(result: Dict[str, Any]) -> str:
    title = (result.get("title") or "").strip()
    snippet = (result.get("snippet") or "").strip()
    if title and snippet:
        return _truncate(f"{title} — {snippet}")
    return _truncate(title or snippet)


def _run_single_query(
    exa: ExaSearchClient,
    query: str,
) -> Tuple[List[Dict[str, Any]], Optional[BaseException]]:
    """Run one Exa search; return (results, error_or_None).

    We swallow the exception locally so the caller can decide whether to
    keep going (partial failure) or escalate (all-queries-failed).
    """
    try:
        results = exa.search(query, num_results=8) or []
        return results, None
    except Exception as e:  # noqa: BLE001 — boundary catch by design
        safe_log_exception(
            logger, e, f"[agent_4] exa search failed for query slot",
        )
        return [], e


def _build_public_claim(result: Dict[str, Any]) -> Optional[Claim]:
    """Promote a single Exa result into a PUBLIC claim, or skip it.

    Returns None if the URL fails the SSRF guard, the snippet has no
    relevant keyword, or the result is missing both title and snippet.
    """
    url = (result.get("url") or "").strip()
    if not url:
        return None
    try:
        assert_safe_url(url)
    except BlockedUrlError as e:
        safe_log_exception(
            logger, e, "[agent_4] dropped Exa result — blocked URL"
        )
        return None

    title = (result.get("title") or "").strip()
    snippet = (result.get("snippet") or "").strip()
    body = f"{title} {snippet}"
    if not _has_signal_keyword(body):
        return None

    text = _claim_text_from_result(result)
    if not text:
        return None

    date = (result.get("published_date") or "").strip() or None
    return Claim(
        text=text,
        source_tag=SourceTag.PUBLIC,
        source_url=url,
        date=date,
    )


async def run(ctx: AgentContext) -> AgentResult:
    """Entry point the dispatcher calls.

    Behavior:
      - Runs four scoped Exa searches.
      - Promotes keyword-matching snippets to PUBLIC claims (URL guarded).
      - Emits one NOT_FOUND claim if everything came back empty.
      - Emits a single ERROR-claim AgentResult only if *all* queries raised.
      - Partial failures: log, drop the failing query, keep going.
    """
    started = time.monotonic()
    exa = ctx.exa_client
    if exa is None:
        # Dispatcher should pass one in. Without an API key we can't do
        # anything useful — emit a clean NOT_FOUND so the section still
        # renders.
        return AgentResult(
            agent_name=AGENT_NAME,
            section_title=SECTION_TITLE,
            claims=[
                Claim(
                    text=(
                        "No Exa client available — skipped customer-facing "
                        "signal search."
                    ),
                    source_tag=SourceTag.NOT_FOUND,
                )
            ],
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    company = (ctx.account_name or "").strip()
    if not company:
        return AgentResult.error(
            AGENT_NAME,
            SECTION_TITLE,
            "agent_4 received empty account_name",
        )

    queries = [
        (slot, tpl.format(company=company)) for slot, tpl in _QUERY_TEMPLATES
    ]

    claims: List[Claim] = []
    failures: List[str] = []
    successes: List[str] = []
    seen_urls: set[str] = set()

    for slot, query in queries:
        results, error = _run_single_query(exa, query)
        if error is not None:
            failures.append(slot)
            continue
        successes.append(slot)

        per_query = 0
        for r in results:
            if per_query >= _MAX_CLAIMS_PER_QUERY:
                break
            if len(claims) >= _MAX_TOTAL_CLAIMS:
                break
            url = (r.get("url") or "").strip()
            if not url or url in seen_urls:
                continue
            try:
                claim = _build_public_claim(r)
            except Exception as e:  # noqa: BLE001 — defensive boundary
                safe_log_exception(
                    logger, e, "[agent_4] failed to build claim from result"
                )
                continue
            if claim is None:
                continue
            claims.append(claim)
            seen_urls.add(url)
            per_query += 1

        if len(claims) >= _MAX_TOTAL_CLAIMS:
            break

    duration_ms = int((time.monotonic() - started) * 1000)

    # All queries raised — total failure.
    if failures and not successes:
        logger.error(
            "[agent_4] all %d Exa queries failed for %s",
            len(queries),
            company,
        )
        return AgentResult.error(
            AGENT_NAME,
            SECTION_TITLE,
            "All Exa queries for customer-facing signals failed.",
        )

    # Some queries succeeded but produced no actionable claims.
    if not claims:
        searched = ", ".join(slot for slot, _ in queries)
        claims.append(
            Claim(
                text=(
                    f"No public customer-facing signals found for "
                    f"{company} across: {searched}."
                ),
                source_tag=SourceTag.NOT_FOUND,
            )
        )

    notes: Optional[str] = None
    if failures:
        notes = f"Partial: {len(failures)}/{len(queries)} Exa queries failed."

    return AgentResult(
        agent_name=AGENT_NAME,
        section_title=SECTION_TITLE,
        claims=claims,
        duration_ms=duration_ms,
        notes=notes,
    )
