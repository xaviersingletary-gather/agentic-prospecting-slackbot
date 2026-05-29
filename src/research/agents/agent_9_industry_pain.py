"""Agent 9 — Industry & Pain Matcher.

Pure deterministic synthesis agent. NO external API calls, NO LLM. Runs in
the second wave (after agents 1–6 have produced their claims) and turns
their corpus of findings into a single industry classification + Gather
AI pain mapping + recommended buyer-persona lead.

Lookup tables (industry → pain, persona pitch-tone rules, and industry →
default persona to lead with) come from the Gather AI SDR skill spec.
This module is the canonical encoding of those tables — do not paraphrase
or trim the strings without updating the spec first.

Behavior:
  1. Walk every Claim across `prior_results`, lowercase the text, and
     concatenate into one corpus string.
  2. For each industry in the table, count weighted keyword hits in the
     corpus. First non-zero match wins; ties broken by total hit count
     (and then by table-declaration order, which is deterministic).
  3. If zero hits across ALL industries, emit a single NOT_FOUND claim
     describing the classification gap and return.
  4. On a successful classification, emit FIVE claims:
       - "Industry: {industry}"            → PUBLIC if we can attach a
         prior-claim source_url to the keyword hit, otherwise INFERRED.
       - "Primary pain: {primary}"         → INFERRED
       - "Secondary pain: {secondary}"     → INFERRED
       - "Mirror language: {list}"         → INFERRED
       - "Recommended persona: {persona}"  → INFERRED, with the full
         pitch-tone rule embedded in inference_logic.

Contract surface:
  - `agent_name = "agent_9_industry_pain"`
  - `section_title = "Industry & Pain Matcher"`
  - Never raises — narrow except + `safe_log_exception` returns an
    `AgentResult.error(...)` if anything unexpected explodes.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from src.research.agents.contract import (
    AgentResult,
    Claim,
    SourceTag,
)
from src.security.exception_logger import safe_log_exception

logger = logging.getLogger(__name__)

AGENT_NAME = "agent_9_industry_pain"
SECTION_TITLE = "Industry & Pain Matcher"


# ---------------------------------------------------------------------------
# Industry → pain mapping (verbatim from Gather AI SDR skill).
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class IndustryProfile:
    """Pain mapping + mirror language + classification keywords."""

    industry: str
    primary_pain: str
    secondary_pain: str
    mirror_language: Tuple[str, ...]
    # Lowercased classification keywords. Substring match against the
    # lowercased corpus of prior claim text.
    keywords: Tuple[str, ...]


# Order is significant — used as the deterministic tiebreaker when two
# industries score the same total. Most-specific industries first.
INDUSTRY_PROFILES: Tuple[IndustryProfile, ...] = (
    IndustryProfile(
        industry="Pharmaceutical",
        primary_pain="Data Privacy + Inventory Accuracy",
        secondary_pain="Cycle Count Time",
        mirror_language=(
            "expiry",
            "lot traceability",
            "FDA audit",
            "batch recall",
            "controlled substance",
            "USP 800",
        ),
        keywords=(
            "pharma",
            "fda",
            "drug",
            "expiry",
            "lot",
            "recall",
            "controlled substance",
            "usp",
        ),
    ),
    IndustryProfile(
        industry="Grocery",
        primary_pain="Write-Offs/Shrinkage + Cycle Count Time",
        secondary_pain="Multi-Site Consistency",
        mirror_language=(
            "FIFO/FEFO",
            "code dates",
            "spoilage",
            "cold chain",
            "shrink",
            "perishable rotation",
        ),
        keywords=(
            "grocery",
            "perishable",
            "fifo",
            "cold chain",
            "shelf life",
            "supermarket",
        ),
    ),
    IndustryProfile(
        industry="Manufacturing",
        primary_pain="Cycle Count Time + System Integration",
        secondary_pain="Manual Processes",
        mirror_language=(
            "line-side",
            "WIP",
            "takt time",
            "supplier-managed inventory",
            "throughput",
        ),
        keywords=(
            "manufactur",
            "factory",
            "wip",
            "production line",
            "takt",
            "supplier-managed",
        ),
    ),
    IndustryProfile(
        industry="Apparel",
        primary_pain="Cycle Count Time + No Real-Time Visibility",
        secondary_pain="Manual Processes",
        mirror_language=(
            "SKU velocity",
            "peak season",
            "color/size matrix",
            "inventory accuracy",
            "returns",
        ),
        keywords=(
            "apparel",
            "clothing",
            "fashion",
            "sku velocity",
            "color size",
            "seasonal",
        ),
    ),
    IndustryProfile(
        industry="3PL",
        primary_pain="Customer Complaints + Inventory Accuracy",
        secondary_pain="Multi-Site Consistency",
        mirror_language=(
            "client SLA",
            "KPI dashboards",
            "account retention",
            "billable accuracy",
            "OTIF",
        ),
        keywords=(
            "3pl",
            "third-party logistics",
            "fulfillment",
            "client sla",
            "otif",
        ),
    ),
    IndustryProfile(
        industry="Electronics",
        primary_pain="Inventory Accuracy + Write-Offs",
        secondary_pain="Data Privacy",
        mirror_language=(
            "serialized inventory",
            "HVHQ items",
            "theft",
            "shrink",
            "asset tracking",
        ),
        keywords=(
            "electronics",
            "serialized",
            "hvhq",
            "high-value",
            "asset tracking",
        ),
    ),
    IndustryProfile(
        industry="Retail Distribution",
        primary_pain="Customer Complaints + No Real-Time Visibility",
        secondary_pain="Multi-Site Consistency",
        mirror_language=(
            "DC throughput",
            "store replenishment",
            "OTIF",
            "allocation accuracy",
        ),
        keywords=(
            "retail",
            "store replenish",
            "dc throughput",
            "allocation",
        ),
    ),
    IndustryProfile(
        industry="CPG",
        primary_pain="No Real-Time Visibility + System Integration",
        secondary_pain="Cycle Count Time",
        mirror_language=(
            "trade promotion lift",
            "allocation",
            "OTIF",
            "S&OP",
            "demand signal",
        ),
        keywords=(
            "cpg",
            "consumer packaged",
            "trade promotion",
            "s&op",
            "demand signal",
        ),
    ),
)


# ---------------------------------------------------------------------------
# Persona pitch-tone rules — verbatim from the spec.
# Keys map Gather AI's TDM/ODM/FS/IT/Safety taxonomy.
# ---------------------------------------------------------------------------

PERSONA_PITCH_RULES: Dict[str, str] = {
    "TDM": (
        "TDM (Technical Decision Maker — Director CI, Industrial Engineer, "
        "WMS Admin): Lead with clean, fast integration — no middleware, "
        "15-min config, no infrastructure changes. Defuse implementation "
        "fear before pitching anything else. Never lead with ROI."
    ),
    "ODM": (
        "ODM (Operational Decision Maker — VP/Director Operations, "
        "Warehouse Manager): Lead with operational friction — cycle "
        "counts, floor visibility, shift-level pain. Never pitch ROI."
    ),
    "FS": (
        "FS (Financial Sponsor — VP/Director Finance): Lead with business "
        "outcome — payback period, labor reduction, network visibility. "
        "Never describe the technology."
    ),
    "IT": (
        "IT (Compliance / IT Stakeholder — VP IT, Director Corporate IT): "
        "Lead with security and integration risk mitigation — ISO 27001, "
        "on-prem option, data flow."
    ),
    "Safety": (
        "Safety (Compliance / Safety — VP Quality, Food Safety Director, "
        "OSHA lead): Lead with safety and certification — drones capture "
        "only pallets and labels. Never lead with efficiency."
    ),
}


# Industry → default persona to lead with (per spec).
INDUSTRY_TO_PERSONA: Dict[str, str] = {
    "Pharmaceutical": "Safety",
    "Electronics": "Safety",
    "Grocery": "ODM",
    "Retail Distribution": "ODM",
    "CPG": "ODM",
    "Manufacturing": "TDM",
    "Apparel": "ODM",
    "3PL": "FS",
}


# ---------------------------------------------------------------------------
# Context + entry point.
# ---------------------------------------------------------------------------


@dataclass
class AgentContext:
    """Context for the Industry & Pain Matcher agent.

    Second-wave agent — takes the completed `AgentResult`s from agents
    1–6 and synthesizes a single classification. No external client.
    """

    account_name: str
    prior_results: List[AgentResult] = field(default_factory=list)
    intent: Optional[str] = None


async def run(ctx: AgentContext) -> AgentResult:
    """Classify industry, map to Gather AI pain, recommend persona.

    Never raises. On unexpected error, returns `AgentResult.error(...)`.
    """
    started = time.monotonic()

    try:
        prior_results = ctx.prior_results or []

        # Build the corpus + per-claim index so we can attach a real
        # source_url to the Industry claim when one of the prior claims
        # that supplied a matched keyword had a URL.
        indexed_claims = _flatten_claims(prior_results)
        corpus = " ".join(text for text, _url in indexed_claims).lower()

        if not corpus.strip():
            return _no_match_result(
                started,
                "No upstream claims supplied — industry classification "
                "could not be performed.",
            )

        winner, hit_count, anchor_url = _classify(indexed_claims, corpus)

        if winner is None:
            return _no_match_result(
                started,
                "Industry classification gap — no keywords from the "
                "Gather AI industry table matched any upstream claim. "
                "Flag for manual classification.",
            )

        claims = _build_classification_claims(winner, anchor_url, hit_count)

        return AgentResult(
            agent_name=AGENT_NAME,
            section_title=SECTION_TITLE,
            claims=claims,
            duration_ms=_elapsed_ms(started),
        )

    except Exception as e:  # noqa: BLE001 — keep section visible
        safe_log_exception(
            logger,
            e,
            "[agent_9] industry-pain synthesis failed",
        )
        return AgentResult.error(
            AGENT_NAME,
            SECTION_TITLE,
            "Industry classification failed.",
        )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _flatten_claims(
    prior_results: List[AgentResult],
) -> List[Tuple[str, Optional[str]]]:
    """Return (text, source_url) tuples for every usable prior claim.

    ERROR / NOT_FOUND claims are skipped — they carry no signal. Both
    PUBLIC and INFERRED (and INTERNAL) claims contribute text to the
    classification corpus; only PUBLIC carries a source_url worth
    attaching to the Industry claim.
    """
    out: List[Tuple[str, Optional[str]]] = []
    for result in prior_results:
        if not isinstance(result, AgentResult):
            continue
        for claim in result.claims or []:
            if not isinstance(claim, Claim):
                continue
            if claim.source_tag in (SourceTag.ERROR, SourceTag.NOT_FOUND):
                continue
            if not claim.text:
                continue
            out.append((claim.text, claim.source_url))
    return out


def _classify(
    indexed_claims: List[Tuple[str, Optional[str]]],
    corpus: str,
) -> Tuple[Optional[IndustryProfile], int, Optional[str]]:
    """Score each industry profile against the corpus.

    Returns (winning_profile, hit_count, anchor_url).
    - winning_profile is None when no industry scored > 0.
    - anchor_url is the first prior claim's source_url whose text
      contained the first matched keyword of the winning industry. None
      if no PUBLIC prior carried the trigger.
    - Tiebreak: total hit count, then declaration order in
      INDUSTRY_PROFILES (which means the "first match wins" intent maps
      cleanly because we iterate the table in order).
    """
    best: Optional[IndustryProfile] = None
    best_score = 0

    for profile in INDUSTRY_PROFILES:
        score = sum(corpus.count(kw) for kw in profile.keywords)
        if score > best_score:
            best = profile
            best_score = score

    if best is None or best_score == 0:
        return None, 0, None

    anchor_url = _find_anchor_url(indexed_claims, best.keywords)
    return best, best_score, anchor_url


def _find_anchor_url(
    indexed_claims: List[Tuple[str, Optional[str]]],
    keywords: Tuple[str, ...],
) -> Optional[str]:
    """Return the first source_url whose claim text matched any keyword.

    The Industry claim is PUBLIC only when we can cite a real upstream
    URL for the classification trigger. Otherwise it must downgrade to
    INFERRED — see the contract: PUBLIC requires source_url.
    """
    for text, url in indexed_claims:
        if not url:
            continue
        lowered = text.lower()
        for kw in keywords:
            if kw in lowered:
                return url
    return None


def _build_classification_claims(
    profile: IndustryProfile,
    anchor_url: Optional[str],
    hit_count: int,
) -> List[Claim]:
    """Emit the 5 claims describing the classification + persona pick."""
    claims: List[Claim] = []

    # 1. Industry classification. PUBLIC if we have a URL, INFERRED otherwise.
    industry_text = f"Industry: {profile.industry}"
    if anchor_url:
        claims.append(
            Claim(
                text=industry_text,
                source_tag=SourceTag.PUBLIC,
                source_url=anchor_url,
                source="prior_results synthesis",
            )
        )
    else:
        claims.append(
            Claim(
                text=industry_text,
                source_tag=SourceTag.INFERRED,
                inference_logic=(
                    f"Keyword match against the Gather AI industry table "
                    f"({hit_count} hit(s)) — no upstream claim with a "
                    f"public URL carried the trigger, so this is the "
                    f"synthesized classification."
                ),
            )
        )

    # 2. Primary pain.
    claims.append(
        Claim(
            text=f"Primary pain: {profile.primary_pain}",
            source_tag=SourceTag.INFERRED,
            inference_logic=(
                f"Industry mapping table per Gather AI SDR playbook "
                f"({profile.industry})"
            ),
        )
    )

    # 3. Secondary pain.
    claims.append(
        Claim(
            text=f"Secondary pain: {profile.secondary_pain}",
            source_tag=SourceTag.INFERRED,
            inference_logic=(
                f"Industry mapping table per Gather AI SDR playbook "
                f"({profile.industry})"
            ),
        )
    )

    # 4. Mirror language.
    mirror_blob = ", ".join(profile.mirror_language)
    claims.append(
        Claim(
            text=f"Mirror language: {mirror_blob}",
            source_tag=SourceTag.INFERRED,
            inference_logic=(
                f"Industry mapping table per Gather AI SDR playbook "
                f"({profile.industry})"
            ),
        )
    )

    # 5. Recommended persona + full pitch-tone rule.
    persona = INDUSTRY_TO_PERSONA.get(profile.industry, "TDM")
    pitch_rule = PERSONA_PITCH_RULES[persona]
    claims.append(
        Claim(
            text=f"Recommended persona: {persona}",
            source_tag=SourceTag.INFERRED,
            inference_logic=(
                f"Industry → persona default per Gather AI SDR playbook "
                f"({profile.industry} → {persona}). Pitch-tone rule: "
                f"{pitch_rule}"
            ),
        )
    )

    return claims


def _no_match_result(started: float, reason: str) -> AgentResult:
    """Single-NOT_FOUND result used when classification can't proceed."""
    return AgentResult(
        agent_name=AGENT_NAME,
        section_title=SECTION_TITLE,
        claims=[
            Claim(
                text=reason,
                source_tag=SourceTag.NOT_FOUND,
            )
        ],
        duration_ms=_elapsed_ms(started),
        notes="Industry classification gap.",
    )
