"""Agent 2 — Operating Baseline.

Pulls revenue, headcount, and growth/expansion/contraction signals.

Sources:
- 10-Ks (or 20-Fs) for public companies via :class:`EdgarClient`.
- News, press, and LinkedIn-via-Exa for private companies and growth
  signals via :class:`ExaSearchClient`.

Emits a `NOT_FOUND` claim explicitly for any category we could not
source — never silently omits a category. On total / per-category
client failure, emits an `ERROR` claim for that category while letting
the others run.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from src.config import settings
from src.integrations.edgar import EdgarClient
from src.integrations.exa.client import ExaSearchClient
from src.research.agents.contract import (
    AgentResult,
    Claim,
    SourceTag,
)
from src.security.exception_logger import safe_log_exception
from src.security.url_guard import BlockedUrlError, assert_safe_url

logger = logging.getLogger(__name__)

AGENT_NAME = "agent_2_operating_baseline"
SECTION_TITLE = "Operating Baseline"

# Industry average revenue per employee — used only for INFERRED headcount
# when we have a sourced revenue number but no headcount source. Roughly
# $250K rev/head is a defensible warehouse/logistics-leaning benchmark.
_REVENUE_PER_HEAD_USD = 250_000


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------


@dataclass
class AgentContext:
    """Inputs Agent 2 needs.

    Kept minimal per the contract — only the fields this agent actually
    reads. Dispatcher fills these from the slash-command payload.
    """

    account_name: str
    account_domain: Optional[str] = None
    intent: Optional[str] = None
    # Optional injected clients — primarily for tests. Production callers
    # pass None and we build defaults.
    edgar_client: Optional[EdgarClient] = None
    exa_client: Optional[ExaSearchClient] = None
    exa_api_key: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_REVENUE_RE = re.compile(
    r"(?:revenue|sales|turnover)[^$0-9]{0,40}"
    r"(\$?\s?\d[\d,\.]*\s?(?:billion|million|bn|m|b)\b)",
    re.IGNORECASE,
)
_HEADCOUNT_RE = re.compile(
    r"(\d[\d,]{2,})\s+(?:employees|workers|staff|people|team\s+members)",
    re.IGNORECASE,
)
_GROWTH_KEYWORDS = (
    "expand",
    "expansion",
    "new facility",
    "new distribution center",
    "opens ",
    "opened ",
    "opening",
    "growth",
    "layoff",
    "layoffs",
    "downsize",
    "close ",
    "closure",
    "shutting",
    "acquisition",
    "acquires",
    "acquired",
    "ipo",
    "funding",
    "raises",
    "raised $",
    "series ",
)

# Market-position phrasing buckets. Lower-cased substring match.
_TOP_1_PHRASES = (
    "#1",
    "no. 1",
    "no.1",
    "number one",
    "number-one",
    "largest",
    "leading",
    "market leader",
    "industry leader",
    "dominant",
)
_TOP_3_PHRASES = (
    "top 3",
    "top three",
    "top-3",
    "top 5",
    "top five",
    "top-5",
    "second-largest",
    "second largest",
    "third-largest",
    "third largest",
    "one of the largest",
    "among the largest",
    "one of the biggest",
    "among the biggest",
)

# Sector vocabulary — used to extract a short sector label from a snippet.
# Order matters: more specific phrases first.
_SECTOR_PHRASES = (
    "us retailer",
    "u.s. retailer",
    "global retailer",
    "global 3pl",
    "global third-party logistics",
    "third-party logistics",
    "3pl",
    "contract logistics",
    "supply chain",
    "logistics",
    "ecommerce",
    "e-commerce",
    "grocery",
    "pharmacy",
    "pharma",
    "healthcare",
    "consumer goods",
    "cpg",
    "apparel",
    "automotive",
    "food and beverage",
    "food & beverage",
    "manufacturer",
    "manufacturing",
    "retailer",
    "retail",
    "industry",
    "sector",
)

# Pull a competitor list. Matches "competitors include X, Y, and Z" /
# "rivals X and Y" / "competes with X, Y" etc.
_COMPETITOR_LIST_RE = re.compile(
    r"(?:competitors?\s+(?:include|are|such\s+as)"
    r"|rivals?\s+(?:include|are|such\s+as)?"
    r"|competes?\s+(?:with|against)"
    r"|key\s+(?:competitors?|rivals?)\s+(?:include|are|such\s+as)?)"
    r"\s*[:\-]?\s*"
    r"([A-Z][A-Za-z0-9&+\.\-'\s]+(?:,\s*(?:and\s+)?[A-Z][A-Za-z0-9&+\.\-'\s]+){0,6})",
    re.IGNORECASE,
)


def _safe_assert(url: str) -> bool:
    """Return True if URL passes SSRF guard, False otherwise. Never raises."""
    try:
        assert_safe_url(url)
        return True
    except BlockedUrlError:
        logger.warning("[agent_2] dropping result — URL blocked by SSRF guard")
        return False
    except Exception as e:  # pragma: no cover — defensive
        safe_log_exception(logger, e, "[agent_2] url guard error")
        return False


def _extract_revenue_snippet(text: str) -> Optional[str]:
    """Pull a revenue figure from a snippet; return the matched substring."""
    if not text:
        return None
    m = _REVENUE_RE.search(text)
    return m.group(0).strip() if m else None


def _extract_headcount(text: str) -> Optional[str]:
    if not text:
        return None
    m = _HEADCOUNT_RE.search(text)
    return f"{m.group(1)} employees" if m else None


def _has_growth_signal(text: str) -> bool:
    if not text:
        return False
    low = text.lower()
    return any(k in low for k in _GROWTH_KEYWORDS)


def _extract_sector(text: str) -> Optional[str]:
    """Pick a short sector label from a snippet. Returns None if nothing
    matches. Kept conservative — we want "global 3PL" / "US retailer" not
    a sentence fragment."""
    if not text:
        return None
    low = text.lower()
    for phrase in _SECTOR_PHRASES:
        if phrase in low:
            return phrase
    return None


def _classify_market_position(text: str) -> Optional[str]:
    """Return one of {"#1", "top 3"} if the snippet supports it, else None.

    Order matters: a snippet containing both "top 3" and "leading" should
    still classify as #1 only when "#1" / "largest" / "market leader"
    appears unambiguously. We bias to top-3 when "one of the largest"
    type phrasing shows up to avoid over-claiming.
    """
    if not text:
        return None
    low = text.lower()
    # Top-3 framing takes precedence when softening phrases appear, so
    # check it first.
    if any(p in low for p in _TOP_3_PHRASES):
        return "top 3"
    if any(p in low for p in _TOP_1_PHRASES):
        return "#1"
    return None


def _extract_named_competitors(text: str) -> List[str]:
    """Return a list of named competitors mentioned in `text`. Empty list
    if nothing parsable. Names are de-duplicated and stripped."""
    if not text:
        return []
    m = _COMPETITOR_LIST_RE.search(text)
    if not m:
        return []
    raw = m.group(1)
    # Split on commas and " and ".
    parts: List[str] = []
    for chunk in re.split(r",|\s+and\s+", raw):
        name = chunk.strip(" .;:-")
        # Drop trailing words like "are major players" that the greedy
        # match might pull in. We accept up to ~4 tokens per name.
        if not name:
            continue
        tokens = name.split()
        if len(tokens) > 4:
            name = " ".join(tokens[:4])
        # Filter out obvious non-name fragments.
        if name.lower() in {"and", "or", "the"}:
            continue
        if name not in parts:
            parts.append(name)
        if len(parts) >= 5:
            break
    return parts


# ---------------------------------------------------------------------------
# Per-category fetchers — each returns either a Claim or None.
# Catching exceptions inside each fetcher means a failure in one category
# doesn't poison the others.
# ---------------------------------------------------------------------------


def _revenue_from_edgar(
    edgar: EdgarClient, account_name: str
) -> Optional[Claim]:
    """Look up the latest 10-K / 20-F. Return a PUBLIC claim if found, else None.

    Missing filings return None (the caller falls back to Exa); only an
    unhandled exception is logged and surfaced as ERROR by the caller.
    """
    filing = edgar.find_latest_10k(account_name)
    if not filing:
        return None
    url = (filing.get("document_url") or "").strip()
    if not url or not _safe_assert(url):
        return None
    period = filing.get("period") or filing.get("file_date") or ""
    form = filing.get("form_type") or "10-K"
    text = (
        f"Latest {form} on file ({period or 'recent'}) — "
        f"see SEC filing for revenue and segment detail."
    )
    return Claim(
        text=text,
        source_tag=SourceTag.PUBLIC,
        source_url=url,
        date=period or None,
    )


def _revenue_from_exa(
    exa: ExaSearchClient, account_name: str
) -> Optional[Claim]:
    """Search news / press for a revenue figure. Returns first viable hit."""
    query = f'"{account_name}" annual revenue OR sales'
    results = exa.search(query, num_results=5)
    for r in results:
        snippet = r.get("snippet") or ""
        url = (r.get("url") or "").strip()
        if not url or not _safe_assert(url):
            continue
        rev = _extract_revenue_snippet(snippet)
        if rev:
            return Claim(
                text=f"Reported revenue: {rev} (per news/press).",
                source_tag=SourceTag.PUBLIC,
                source_url=url,
                date=r.get("published_date") or None,
            )
    return None


def _headcount_from_exa(
    exa: ExaSearchClient, account_name: str
) -> Optional[Claim]:
    """Search LinkedIn-via-Exa, company sites, and press for headcount."""
    query = f'"{account_name}" employees OR headcount OR "team of"'
    results = exa.search(query, num_results=5)
    for r in results:
        snippet = r.get("snippet") or ""
        url = (r.get("url") or "").strip()
        if not url or not _safe_assert(url):
            continue
        hc = _extract_headcount(snippet)
        if hc:
            return Claim(
                text=f"Headcount: {hc}.",
                source_tag=SourceTag.PUBLIC,
                source_url=url,
                date=r.get("published_date") or None,
            )
    return None


def _growth_claims_from_exa(
    exa: ExaSearchClient, account_name: str
) -> List[Claim]:
    """Search press for expansion / contraction / funding signals."""
    query = (
        f'"{account_name}" expansion OR layoffs OR '
        f'"new distribution center" OR acquisition'
    )
    results = exa.search(query, num_results=5)
    out: List[Claim] = []
    for r in results:
        snippet = r.get("snippet") or ""
        url = (r.get("url") or "").strip()
        title = r.get("title") or ""
        combined = f"{title} — {snippet}".strip(" —")
        if not url or not _safe_assert(url):
            continue
        if _has_growth_signal(combined):
            text = combined[:240] if combined else f"Growth signal for {account_name}"
            out.append(
                Claim(
                    text=text,
                    source_tag=SourceTag.PUBLIC,
                    source_url=url,
                    date=r.get("published_date") or None,
                )
            )
        if len(out) >= 3:
            break
    return out


def _market_position_from_exa(
    exa: ExaSearchClient, account_name: str
) -> Dict[str, Any]:
    """Search Exa for market-share / ranking signals.

    Returns a dict with keys:
      - claim:  the market-position Claim to emit (PUBLIC / INFERRED / NOT_FOUND)
      - competitors_claim:  the competitor Claim to emit (PUBLIC if a list
        surfaced in the same snippet; otherwise None — the caller decides
        whether to fall back to INFERRED based on its own search).
      - errored: True if the underlying client raised before completing.
    """
    query = (
        f"{account_name} market share ranking position competitors 2024 2025"
    )
    try:
        results = exa.search(query, num_results=5) or []
    except Exception as e:
        safe_log_exception(
            logger, e, "[agent_2] Exa market-position lookup failed"
        )
        return {"claim": None, "competitors_claim": None, "errored": True}

    for r in results:
        snippet = r.get("snippet") or ""
        title = r.get("title") or ""
        combined = f"{title}. {snippet}".strip(". ")
        url = (r.get("url") or "").strip()
        if not url or not _safe_assert(url):
            continue
        position = _classify_market_position(combined)
        sector = _extract_sector(combined)
        if position is None or sector is None:
            continue
        text = f"{position} in {sector}"
        market_claim = Claim(
            text=text,
            source_tag=SourceTag.PUBLIC,
            source_url=url,
            date=r.get("published_date") or None,
        )
        # See if competitor names sit in the same snippet.
        names = _extract_named_competitors(combined)
        competitors_claim: Optional[Claim] = None
        if names:
            competitors_claim = Claim(
                text=f"Named competitors in sector: {', '.join(names)}",
                source_tag=SourceTag.PUBLIC,
                source_url=url,
                date=r.get("published_date") or None,
            )
        return {
            "claim": market_claim,
            "competitors_claim": competitors_claim,
            "errored": False,
        }

    return {"claim": None, "competitors_claim": None, "errored": False}


def _competitors_from_exa(
    exa: ExaSearchClient, account_name: str
) -> Dict[str, Any]:
    """Second dedicated query for named competitors.

    Used either when the market-position query didn't surface a list, or
    to enrich the market-position claim. Returns dict shaped like
    `_market_position_from_exa` but only populates `competitors_claim`.
    """
    query = (
        f"{account_name} top competitors industry rivals leaders sector"
    )
    try:
        results = exa.search(query, num_results=5) or []
    except Exception as e:
        safe_log_exception(
            logger, e, "[agent_2] Exa competitors lookup failed"
        )
        return {"competitors_claim": None, "errored": True}

    for r in results:
        snippet = r.get("snippet") or ""
        title = r.get("title") or ""
        combined = f"{title}. {snippet}".strip(". ")
        url = (r.get("url") or "").strip()
        if not url or not _safe_assert(url):
            continue
        names = _extract_named_competitors(combined)
        if not names:
            continue
        return {
            "competitors_claim": Claim(
                text=f"Named competitors in sector: {', '.join(names)}",
                source_tag=SourceTag.PUBLIC,
                source_url=url,
                date=r.get("published_date") or None,
            ),
            "errored": False,
        }
    return {"competitors_claim": None, "errored": False}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def run(ctx: AgentContext) -> AgentResult:
    """Build the Operating Baseline section.

    Per-category exception handling: each fetcher runs inside its own
    try/except so a 5xx from Exa for headcount doesn't kill the revenue
    section we already pulled from EDGAR.
    """
    started = time.time()
    account = (ctx.account_name or "").strip()
    if not account:
        return AgentResult.error(
            AGENT_NAME, SECTION_TITLE, "Missing account_name in context"
        )

    edgar = ctx.edgar_client or EdgarClient()
    # Fall back through ctx.exa_api_key → settings.EXA_API_KEY → env var so
    # production (which leaves ctx.exa_api_key unset) still picks up the key.
    _exa_key = ctx.exa_api_key or settings.EXA_API_KEY
    exa = ctx.exa_client or ExaSearchClient(api_key=_exa_key or "")

    claims: List[Claim] = []

    # --- Revenue: EDGAR first, then Exa news ---
    revenue_claim: Optional[Claim] = None
    revenue_errored = False
    try:
        revenue_claim = _revenue_from_edgar(edgar, account)
    except Exception as e:
        safe_log_exception(logger, e, "[agent_2] EDGAR revenue lookup failed")
        # Don't mark errored yet — Exa fallback may still succeed.

    if revenue_claim is None:
        try:
            revenue_claim = _revenue_from_exa(exa, account)
        except Exception as e:
            safe_log_exception(logger, e, "[agent_2] Exa revenue lookup failed")
            revenue_errored = True

    if revenue_claim is not None:
        claims.append(revenue_claim)
    elif revenue_errored:
        claims.append(
            Claim(
                text="Revenue lookup failed (external search error).",
                source_tag=SourceTag.ERROR,
            )
        )
    else:
        claims.append(
            Claim(
                text=(
                    f"No public revenue figure found for {account} "
                    "(searched SEC filings + news / press)."
                ),
                source_tag=SourceTag.NOT_FOUND,
            )
        )

    # --- Headcount: Exa (LinkedIn / company site / press) ---
    headcount_claim: Optional[Claim] = None
    headcount_errored = False
    try:
        headcount_claim = _headcount_from_exa(exa, account)
    except Exception as e:
        safe_log_exception(logger, e, "[agent_2] Exa headcount lookup failed")
        headcount_errored = True

    if headcount_claim is not None:
        claims.append(headcount_claim)
    elif headcount_errored:
        claims.append(
            Claim(
                text="Headcount lookup failed (external search error).",
                source_tag=SourceTag.ERROR,
            )
        )
    else:
        # Try INFERRED fallback if we have a sourced revenue figure with $.
        inferred = _maybe_infer_headcount(revenue_claim)
        if inferred is not None:
            claims.append(inferred)
        else:
            claims.append(
                Claim(
                    text=(
                        f"No public headcount figure found for {account} "
                        "(searched LinkedIn-via-Exa, company site, press)."
                    ),
                    source_tag=SourceTag.NOT_FOUND,
                )
            )

    # --- Growth signals: Exa press ---
    growth_claims: List[Claim] = []
    growth_errored = False
    try:
        growth_claims = _growth_claims_from_exa(exa, account)
    except Exception as e:
        safe_log_exception(logger, e, "[agent_2] Exa growth lookup failed")
        growth_errored = True

    if growth_claims:
        claims.extend(growth_claims)
    elif growth_errored:
        claims.append(
            Claim(
                text="Growth-signal lookup failed (external search error).",
                source_tag=SourceTag.ERROR,
            )
        )
    else:
        claims.append(
            Claim(
                text=(
                    f"No recent expansion / contraction / funding signals "
                    f"found for {account}."
                ),
                source_tag=SourceTag.NOT_FOUND,
            )
        )

    # --- Market position + named competitors ---
    # Two dedicated Exa queries. Per-fetcher try/except already inside
    # each helper, so we never leak an exception here.
    market_position_result = _market_position_from_exa(exa, account)
    competitors_result = _competitors_from_exa(exa, account)

    market_claim: Optional[Claim] = market_position_result.get("claim")
    competitors_claim: Optional[Claim] = (
        market_position_result.get("competitors_claim")
        or competitors_result.get("competitors_claim")
    )
    market_errored = bool(market_position_result.get("errored"))
    competitors_errored = bool(competitors_result.get("errored"))

    # Market position claim emission.
    if market_claim is not None:
        claims.append(market_claim)
    elif market_errored and competitors_errored:
        # Both upstream calls failed — surface an ERROR slot so the
        # section isn't silently missing.
        claims.append(
            Claim(
                text=(
                    "Market-position lookup failed "
                    "(external search error)."
                ),
                source_tag=SourceTag.ERROR,
            )
        )
    else:
        # Try to infer sector from any PUBLIC growth/headcount/revenue
        # snippet we already captured. If we can identify a sector,
        # default to challenger framing; otherwise NOT_FOUND.
        inferred_sector: Optional[str] = None
        for existing in claims:
            if existing.source_tag is SourceTag.PUBLIC:
                inferred_sector = _extract_sector(existing.text or "")
                if inferred_sector:
                    break
        if inferred_sector:
            claims.append(
                Claim(
                    text=(
                        f"Challenger position in {inferred_sector} "
                        f"(no top-3 signal sourced)"
                    ),
                    source_tag=SourceTag.INFERRED,
                    inference_logic=(
                        "No public top-N ranking surfaced; "
                        "defaulting to challenger framing."
                    ),
                )
            )
        else:
            claims.append(
                Claim(
                    text=(
                        f"No public market-position signal found for "
                        f"{account} (sector not identified)."
                    ),
                    source_tag=SourceTag.NOT_FOUND,
                )
            )

    # Competitors claim emission.
    if competitors_claim is not None:
        claims.append(competitors_claim)
    elif competitors_errored and market_errored:
        claims.append(
            Claim(
                text=(
                    "Named-competitors lookup failed "
                    "(external search error)."
                ),
                source_tag=SourceTag.ERROR,
            )
        )
    else:
        claims.append(
            Claim(
                text=(
                    f"No named competitors surfaced for {account} "
                    "(no industry-research article sourced)."
                ),
                source_tag=SourceTag.NOT_FOUND,
            )
        )

    return AgentResult(
        agent_name=AGENT_NAME,
        section_title=SECTION_TITLE,
        claims=claims,
        duration_ms=int((time.time() - started) * 1000),
    )


# ---------------------------------------------------------------------------
# Inference helper (kept at module bottom so it's easy to remove if the
# inferred-headcount heuristic proves unreliable in practice).
# ---------------------------------------------------------------------------


_REV_NUM_RE = re.compile(
    r"\$\s?(\d+(?:\.\d+)?)\s?(billion|million|bn|b|m)\b",
    re.IGNORECASE,
)


def _maybe_infer_headcount(revenue_claim: Optional[Claim]) -> Optional[Claim]:
    """If revenue_claim contains a $X billion / $X million figure, produce
    an INFERRED headcount from the revenue-per-head benchmark."""
    if revenue_claim is None or revenue_claim.source_tag is not SourceTag.PUBLIC:
        return None
    m = _REV_NUM_RE.search(revenue_claim.text or "")
    if not m:
        return None
    try:
        n = float(m.group(1))
    except ValueError:
        return None
    unit = m.group(2).lower()
    if unit.startswith("b"):
        revenue_usd = n * 1_000_000_000
    else:
        revenue_usd = n * 1_000_000
    if revenue_usd <= 0:
        return None
    est_heads = int(revenue_usd / _REVENUE_PER_HEAD_USD)
    # Round to nearest thousand for honesty about precision.
    rounded = max(1000, int(round(est_heads, -3)))
    return Claim(
        text=f"Estimated headcount ~{rounded:,} (inferred from revenue).",
        source_tag=SourceTag.INFERRED,
        inference_logic=(
            f"Revenue ${revenue_usd:,.0f} / "
            f"${_REVENUE_PER_HEAD_USD:,} avg revenue per employee "
            "(warehouse/logistics benchmark)."
        ),
        source_url=revenue_claim.source_url,
    )
