"""Agent 1 — Network Footprint.

Pulls DC count, square footage, and a derived pallet-move estimate. The
spec (May 26, §6 Agent 1) calls for public filings, company site, press
releases, BuiltWith-style infra signals, plus Google Maps for sqft. Per
Xavier's confirmation, V1 ships **without** Google Maps — we lean on Exa
for sqft signals and fall back to industry benchmarks as INFERRED claims.

Source-attribution rules from `docs/agent-contract.md`:

- DC count emitted as PUBLIC when we find a sourceable URL with a number.
- Sqft emitted as PUBLIC when sourced, INFERRED when only the industry
  is known, NOT_FOUND when neither is available.
- Pallet-move estimate is always INFERRED (math = `sqft × 0.60 × 4 / 36`,
  from `src/agents/researcher.py::calculate_exception_tax`).

Tests live at `tests/phase20_agents/agent_1/` and mock the Exa client at
this module's import path.
"""
from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from src.integrations.exa.client import ExaSearchClient
from src.research.agents.contract import AgentResult, Claim, SourceTag
from src.security.exception_logger import safe_log_exception
from src.security.url_guard import BlockedUrlError, assert_safe_url

logger = logging.getLogger(__name__)

AGENT_NAME = "agent_1_network_footprint"
SECTION_TITLE = "Network Footprint"

# Industry sqft-per-DC benchmarks (per-facility averages). Used only when
# the agent can identify the industry but cannot source a hard sqft number.
# Source: Xavier's spec deviation note, May 26 worker briefing.
INDUSTRY_SQFT_BENCHMARK: Dict[str, int] = {
    "grocery": 750_000,
    "retail": 600_000,
    "3pl": 400_000,
    "logistics": 400_000,
    "manufacturing": 350_000,
}

# Pallet-move math (see src/agents/researcher.py::calculate_exception_tax).
#   positions = sqft × 0.60 × 4 / 36
_PALLET_FACTOR = 0.60 * 4 / 36  # ~0.0667 positions per sqft

# A loose regex pulling integer counts out of "operates N distribution
# centers" / "210 distribution centers" / "47 DCs" style snippets. We do
# NOT trust this for anything — it just lets us prefer Exa snippets that
# actually mention a number when picking which result becomes the claim.
_DC_COUNT_RE = re.compile(
    r"(?P<num>\d{1,4})\s*(?:\+)?\s*"
    r"(?:distribution\s+centers?|fulfillment\s+centers?|DCs?|"
    r"warehouses?|facilities)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Agent context
# ---------------------------------------------------------------------------


@dataclass
class AgentContext:
    """Inputs Agent 1 needs from the dispatcher.

    Per the contract doc each agent declares its own context dataclass so
    the dispatcher only fills the fields actually used. Agent 1 needs the
    account name to search, plus an optional industry hint (which the
    operating-baseline agent or the rep may supply) to pick a benchmark
    when sqft isn't sourceable.
    """

    account_name: str
    account_domain: Optional[str] = None
    intent: Optional[str] = None
    industry: Optional[str] = None  # e.g. "grocery", "3pl", "retail"
    exa_client: Optional[ExaSearchClient] = None


# ---------------------------------------------------------------------------
# Exa wrappers — every call returns ([], error_flag) on failure rather than
# raising. The agent collapses to a single ERROR result only when EVERY
# search blew up.
# ---------------------------------------------------------------------------


def _safe_search(
    client: ExaSearchClient, query: str
) -> Tuple[List[Dict[str, Any]], bool]:
    """Run one Exa query, swallow exceptions, return (results, errored)."""
    try:
        results = client.search(query, num_results=6) or []
    except Exception as exc:  # noqa: BLE001 — boundary catch by design
        safe_log_exception(
            logger, exc, f"[{AGENT_NAME}] exa.search failed"
        )
        return [], True
    # The Exa client already runs SSRF guard on URLs it returns, but the
    # agent stores URLs that may be re-emitted; double-check here.
    cleaned: List[Dict[str, Any]] = []
    for r in results:
        url = (r.get("url") or "").strip()
        if not url:
            continue
        try:
            assert_safe_url(url)
        except BlockedUrlError:
            continue
        cleaned.append(r)
    return cleaned, False


def _pick_dc_count_result(
    results: List[Dict[str, Any]],
) -> Optional[Tuple[int, Dict[str, Any]]]:
    """Return (count, result_dict) for the highest-confidence DC-count
    snippet found in `results`, or None."""
    best: Optional[Tuple[int, Dict[str, Any]]] = None
    for r in results:
        snippet = f"{r.get('title') or ''} {r.get('snippet') or ''}"
        m = _DC_COUNT_RE.search(snippet)
        if not m:
            continue
        try:
            num = int(m.group("num"))
        except (TypeError, ValueError):
            continue
        # Reject obviously bogus values (e.g. "2024 distribution centers")
        if num < 2 or num > 5000:
            continue
        if best is None or num > best[0]:
            # Prefer larger counts — public filings tend to cite the full
            # network, while one-off press releases cite a single new DC.
            best = (num, r)
    return best


def _pick_sqft_result(
    results: List[Dict[str, Any]],
) -> Optional[Tuple[int, Dict[str, Any]]]:
    """Pull a million/thousand sqft figure out of any snippet."""
    sqft_re = re.compile(
        r"(?P<num>\d{1,3}(?:[,.]\d{3})*|\d+(?:\.\d+)?)\s*"
        r"(?P<unit>million|m|thousand|k)?\s*(?:square\s*feet|sq\.?\s*ft\.?|sqft)",
        re.IGNORECASE,
    )
    best: Optional[Tuple[int, Dict[str, Any]]] = None
    for r in results:
        snippet = f"{r.get('title') or ''} {r.get('snippet') or ''}"
        m = sqft_re.search(snippet)
        if not m:
            continue
        raw = m.group("num").replace(",", "")
        try:
            base = float(raw)
        except ValueError:
            continue
        unit = (m.group("unit") or "").lower()
        if unit in ("million", "m"):
            base *= 1_000_000
        elif unit in ("thousand", "k"):
            base *= 1_000
        val = int(base)
        if val < 10_000:  # too small to be a DC footprint
            continue
        if best is None or val > best[0]:
            best = (val, r)
    return best


def _normalize_industry(industry: Optional[str]) -> Optional[str]:
    if not industry:
        return None
    low = industry.strip().lower()
    for key in INDUSTRY_SQFT_BENCHMARK:
        if key in low:
            return key
    return None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def run(ctx: AgentContext) -> AgentResult:
    """Build the Network Footprint section for `ctx.account_name`.

    The agent issues three Exa searches (DC count, sqft, press releases),
    extracts numbers where present, and emits one claim per data point.
    On total Exa failure we return AgentResult.error(...). When Exa
    returns nothing useful we emit NOT_FOUND / INFERRED claims so the
    section is never silently dropped (CLAUDE.md → 'Never omit a
    research section').
    """
    started = time.monotonic()
    client = ctx.exa_client or ExaSearchClient(os.getenv("EXA_API_KEY", ""))

    name = ctx.account_name
    dc_query = (
        f'"{name}" distribution centers OR fulfillment centers OR warehouses'
    )
    sqft_query = f'"{name}" warehouse square feet OR sqft OR footprint'
    locations_query = (
        f'"{name}" distribution center locations OR new facility'
    )

    dc_results, dc_err = _safe_search(client, dc_query)
    sqft_results, sqft_err = _safe_search(client, sqft_query)
    loc_results, loc_err = _safe_search(client, locations_query)

    if dc_err and sqft_err and loc_err:
        # Every search blew up — surface the failure rather than guess.
        return AgentResult.error(
            AGENT_NAME,
            SECTION_TITLE,
            "Network footprint search failed — Exa unavailable.",
        )

    claims: List[Claim] = []

    # ---- DC count ------------------------------------------------------
    dc_pick = _pick_dc_count_result(dc_results)
    dc_count: Optional[int] = None
    if dc_pick is not None:
        dc_count, dc_src = dc_pick
        claims.append(
            Claim(
                text=f"{dc_count} distribution / fulfillment facilities in the network.",
                source_tag=SourceTag.PUBLIC,
                source_url=dc_src["url"],
                date=(dc_src.get("published_date") or None) or None,
            )
        )
    else:
        claims.append(
            Claim(
                text=(
                    "No public DC count located via Exa searches on "
                    "press releases, 10-K excerpts, or the company site."
                ),
                source_tag=SourceTag.NOT_FOUND,
            )
        )

    # ---- Square footage ------------------------------------------------
    sqft_pick = _pick_sqft_result(sqft_results)
    total_sqft: Optional[int] = None
    if sqft_pick is not None:
        total_sqft, sqft_src = sqft_pick
        claims.append(
            Claim(
                text=f"~{total_sqft:,} sq ft of warehouse footprint disclosed.",
                source_tag=SourceTag.PUBLIC,
                source_url=sqft_src["url"],
                date=(sqft_src.get("published_date") or None) or None,
            )
        )
    else:
        # Try the benchmark fallback. Requires both a known industry and
        # a DC count (sourced or otherwise) to do meaningful math.
        industry_key = _normalize_industry(ctx.industry)
        if industry_key and dc_count:
            per_dc = INDUSTRY_SQFT_BENCHMARK[industry_key]
            total_sqft = dc_count * per_dc
            claims.append(
                Claim(
                    text=(
                        f"~{total_sqft:,} sq ft estimated total footprint "
                        f"({dc_count} facilities × {per_dc:,} sqft/DC "
                        f"{industry_key} benchmark)."
                    ),
                    source_tag=SourceTag.INFERRED,
                    inference_logic=(
                        f"DC count {dc_count} × industry benchmark "
                        f"{per_dc:,} sqft/facility ({industry_key}); "
                        "no public sqft figure sourced via Exa."
                    ),
                )
            )
        elif industry_key and not dc_count:
            # Industry known but no DC count — infer per-DC only.
            per_dc = INDUSTRY_SQFT_BENCHMARK[industry_key]
            claims.append(
                Claim(
                    text=(
                        f"~{per_dc:,} sq ft per facility expected for "
                        f"{industry_key} operators."
                    ),
                    source_tag=SourceTag.INFERRED,
                    inference_logic=(
                        f"Industry benchmark: {industry_key} DCs ~{per_dc:,} sqft. "
                        "No public per-DC figure sourced."
                    ),
                )
            )
        else:
            claims.append(
                Claim(
                    text=(
                        "No public square-footage figure sourced and "
                        "industry not specified — sqft benchmark cannot "
                        "be applied. Ask the rep for industry/vertical."
                    ),
                    source_tag=SourceTag.NOT_FOUND,
                )
            )

    # ---- Pallet-move estimate (always INFERRED if we have any sqft) ---
    if total_sqft:
        positions = int(total_sqft * _PALLET_FACTOR)
        claims.append(
            Claim(
                text=(
                    f"~{positions:,} pallet positions across the network "
                    "(rough working-backwards estimate)."
                ),
                source_tag=SourceTag.INFERRED,
                inference_logic=(
                    f"positions = sqft × 0.60 × 4 / 36 = "
                    f"{total_sqft:,} × 0.0667 ≈ {positions:,}. "
                    "Formula from src/agents/researcher.py:"
                    "calculate_exception_tax."
                ),
            )
        )
    else:
        claims.append(
            Claim(
                text=(
                    "Pallet position estimate not produced — no sourced "
                    "or inferred sqft to plug into the formula."
                ),
                source_tag=SourceTag.NOT_FOUND,
            )
        )

    # ---- Location breadcrumbs (best-effort; one PUBLIC claim if found)
    if loc_results:
        top = loc_results[0]
        if top.get("snippet"):
            claims.append(
                Claim(
                    text=(
                        f"Recent facility/location signal: "
                        f"{top['snippet'][:200].strip()}"
                    ),
                    source_tag=SourceTag.PUBLIC,
                    source_url=top["url"],
                    date=(top.get("published_date") or None) or None,
                )
            )

    duration_ms = int((time.monotonic() - started) * 1000)
    notes: Optional[str] = None
    partial = [bool(dc_err), bool(sqft_err), bool(loc_err)]
    if any(partial) and not all(partial):
        notes = "Partial Exa failure during network-footprint research."

    return AgentResult(
        agent_name=AGENT_NAME,
        section_title=SECTION_TITLE,
        claims=claims,
        duration_ms=duration_ms,
        notes=notes,
    )
