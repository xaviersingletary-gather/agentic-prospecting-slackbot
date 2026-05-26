"""Agent 5 — Shrink & Compliance.

Surfaces audit exposure, regulatory pressure (especially F&B, pharma,
healthcare), known shrink events, and recall history for the target
account. Sources: 10-K Risk Factors quotes, FDA warning letters, recall
databases, and regulatory news.

Per the May 26 spec §6 Agent 5. Tagged claims only — no silent inference.
Industry-implied exposure (e.g. "F&B company → FDA audit risk applies
generically") emits INFERRED with explicit `inference_logic`.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import List, Optional

from src.integrations.edgar import EdgarClient
from src.integrations.exa.client import ExaSearchClient
from src.research.agents.contract import AgentResult, Claim, SourceTag
from src.security.exception_logger import safe_log_exception
from src.security.url_guard import BlockedUrlError, assert_safe_url
from src.utils.document_fetcher import extract_10k_sections, fetch_html

logger = logging.getLogger(__name__)

AGENT_NAME = "agent_5_shrink_compliance"
SECTION_TITLE = "Shrink & Compliance"

# Industries with FDA jurisdiction. INFERRED claim only applies to these.
_FDA_INDUSTRIES = {
    "food",
    "food & beverage",
    "food and beverage",
    "f&b",
    "beverage",
    "pharma",
    "pharmaceutical",
    "pharmaceuticals",
    "healthcare",
    "health care",
    "medical device",
    "medical devices",
    "biotech",
    "biotechnology",
    "nutraceutical",
}

# Keywords we hunt for in the Risk Factors section.
_RISK_KEYWORDS = [
    "shrink",
    "shrinkage",
    "inventory write-down",
    "inventory write down",
    "write-down",
    "audit",
    "fda",
    "recall",
    "warning letter",
    "regulatory",
    "compliance",
    "product safety",
]


@dataclass
class AgentContext:
    """Inputs Agent 5 needs to do its job.

    Defined per-agent rather than shared so the dispatcher only fills the
    fields actually used. The Exa client is supplied at runtime (tests
    inject a mock); the EDGAR client is constructed inside `run()` from
    the import-path so tests can patch it.
    """

    account_name: str
    account_domain: Optional[str] = None
    intent: Optional[str] = None
    industry: Optional[str] = None
    exa_client: Optional[ExaSearchClient] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_fda_industry(industry: Optional[str]) -> bool:
    if not industry:
        return False
    lowered = industry.lower()
    return any(token in lowered for token in _FDA_INDUSTRIES)


def _safe_assert_url(url: str) -> Optional[str]:
    """Return the URL if SSRF-safe, else None. Logs blocks at info level."""
    if not url:
        return None
    try:
        assert_safe_url(url)
        return url
    except BlockedUrlError:
        logger.info("[%s] dropped url — blocked by SSRF guard", AGENT_NAME)
        return None


def _quote_window(text: str, keyword: str, window: int = 240) -> Optional[str]:
    """Return a short quote around `keyword` inside `text`, or None."""
    if not text or not keyword:
        return None
    lowered = text.lower()
    kw_lower = keyword.lower()
    idx = lowered.find(kw_lower)
    if idx < 0:
        return None
    start = max(0, idx - window // 2)
    end = min(len(text), idx + len(keyword) + window // 2)
    snippet = text[start:end].strip()
    # Collapse whitespace.
    snippet = re.sub(r"\s+", " ", snippet)
    # Cap hard so we never accidentally emit the full risk section.
    return snippet[:320]


def _scan_risk_factors_for_quotes(risk_text: str) -> List[tuple[str, str]]:
    """Return up to 3 (keyword, short_quote) pairs from the risk-factors text."""
    if not risk_text:
        return []
    seen_quotes: set[str] = set()
    results: List[tuple[str, str]] = []
    for kw in _RISK_KEYWORDS:
        quote = _quote_window(risk_text, kw)
        if not quote:
            continue
        # De-dupe near-identical windows (different keywords hitting the
        # same paragraph would otherwise emit redundant claims).
        signature = quote[:80].lower()
        if signature in seen_quotes:
            continue
        seen_quotes.add(signature)
        results.append((kw, quote))
        if len(results) >= 3:
            break
    return results


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------


async def run(ctx: AgentContext) -> AgentResult:
    """Execute Agent 5.

    Defensive at every external boundary. EDGAR failure does NOT stop
    the Exa branch (and vice-versa) — we surface partial results so the
    rep still gets something.
    """
    start = time.monotonic()
    claims: List[Claim] = []
    notes_parts: List[str] = []
    searches_tried: List[str] = []

    # ---- EDGAR branch ----------------------------------------------------
    edgar_client = EdgarClient()
    edgar_result = None
    try:
        edgar_result = edgar_client.find_latest_10k(ctx.account_name)
    except Exception as e:
        safe_log_exception(
            logger,
            e,
            f"[{AGENT_NAME}] EDGAR find_latest_10k failed for "
            f"'{ctx.account_name}'",
        )
        notes_parts.append("EDGAR lookup failed")

    if edgar_result and edgar_result.get("document_url"):
        doc_url = _safe_assert_url(edgar_result["document_url"])
        period = edgar_result.get("period") or edgar_result.get("file_date") or None
        if doc_url:
            try:
                html = fetch_html(doc_url)
            except Exception as e:
                safe_log_exception(
                    logger,
                    e,
                    f"[{AGENT_NAME}] fetch_html raised for 10-K of "
                    f"'{ctx.account_name}'",
                )
                html = None

            if html:
                try:
                    sections = extract_10k_sections(html)
                except Exception as e:
                    safe_log_exception(
                        logger,
                        e,
                        f"[{AGENT_NAME}] extract_10k_sections failed",
                    )
                    sections = {}

                risk_text = sections.get("risk_factors") or ""
                quotes = _scan_risk_factors_for_quotes(risk_text)
                for kw, quote in quotes:
                    try:
                        claims.append(
                            Claim(
                                text=(
                                    f"10-K Risk Factors mentions \"{kw}\": "
                                    f"\"{quote}\""
                                ),
                                source_tag=SourceTag.PUBLIC,
                                source_url=doc_url,
                                date=period,
                            )
                        )
                    except ValueError as e:
                        safe_log_exception(
                            logger,
                            e,
                            f"[{AGENT_NAME}] dropped malformed 10-K claim",
                        )
            else:
                notes_parts.append("10-K fetched but body unavailable")

    # ---- Exa branch ------------------------------------------------------
    exa = ctx.exa_client
    seen_urls: set[str] = set()
    if exa is not None:
        for query in (
            f"{ctx.account_name} FDA warning letter recall 2024 2025 2026",
            f"{ctx.account_name} shrink inventory write-down audit exposure",
            f"{ctx.account_name} recall product safety regulatory action",
        ):
            searches_tried.append(query)
            try:
                hits = exa.search(query, num_results=5) or []
            except Exception as e:
                safe_log_exception(
                    logger,
                    e,
                    f"[{AGENT_NAME}] Exa search failed: {query[:80]}",
                )
                continue

            for hit in hits[:2]:  # cap per-query to keep blob compact
                url = _safe_assert_url((hit or {}).get("url") or "")
                if not url:
                    continue
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                snippet = ((hit or {}).get("snippet") or "").strip()
                title = ((hit or {}).get("title") or "").strip()
                published = ((hit or {}).get("published_date") or "") or None
                text_body = snippet or title
                if not text_body:
                    continue
                try:
                    claims.append(
                        Claim(
                            text=text_body[:320],
                            source_tag=SourceTag.PUBLIC,
                            source_url=url,
                            date=published,
                        )
                    )
                except ValueError as e:
                    safe_log_exception(
                        logger,
                        e,
                        f"[{AGENT_NAME}] dropped malformed Exa claim",
                    )

    # ---- Industry-implied FDA inference (sparingly) ----------------------
    has_fda_evidence = any(
        "fda" in c.text.lower() or "warning letter" in c.text.lower()
        or "recall" in c.text.lower()
        for c in claims
    )
    if _is_fda_industry(ctx.industry) and not has_fda_evidence:
        try:
            claims.append(
                Claim(
                    text=(
                        "Industry-implied FDA audit / recall exposure — no "
                        "public regulatory action was sourced for this "
                        "account, but companies in this vertical operate "
                        "under FDA jurisdiction."
                    ),
                    source_tag=SourceTag.INFERRED,
                    inference_logic=(
                        f"industry='{ctx.industry}' falls within FDA "
                        "jurisdiction (food, pharma, healthcare, biotech, "
                        "or medical devices); audit/recall risk applies "
                        "generically even without a specific sourced event."
                    ),
                )
            )
        except ValueError as e:
            safe_log_exception(
                logger,
                e,
                f"[{AGENT_NAME}] dropped malformed INFERRED industry claim",
            )

    # ---- NOT_FOUND fallback ---------------------------------------------
    if not claims:
        searched_summary = (
            "Searched EDGAR 10-K Risk Factors and Exa for FDA warning "
            "letters, recalls, shrink events, and audit exposure — no "
            "sourced findings."
        )
        if searches_tried:
            searched_summary += (
                f" Queries tried: {len(searches_tried)} Exa searches."
            )
        claims.append(
            Claim(text=searched_summary, source_tag=SourceTag.NOT_FOUND)
        )

    duration_ms = int((time.monotonic() - start) * 1000)
    return AgentResult(
        agent_name=AGENT_NAME,
        section_title=SECTION_TITLE,
        claims=claims,
        duration_ms=duration_ms,
        notes="; ".join(notes_parts) if notes_parts else None,
    )
