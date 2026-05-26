"""Agent 7 — Department Angles / Personas.

Translates the outputs of agents 1–6 into department-specific pain
framing for Gather AI's five buyer personas:

  - TDM    — Technical Decision Maker (CI / Industrial Engineering /
             Automation directors). Primary entry point in new logo deals.
  - ODM    — Operational Decision Maker (VP/Director of Ops, Warehouse,
             Inventory Control, ICQA). Owns adoption on the floor.
  - FS     — Financial Sponsor (VP Finance, Executive Director Finance).
             #1 closed-lost gap when missed.
  - IT     — IT stakeholder (VP/Director IT, Plant-level IT). Data flow,
             security, integration health — veto risk.
  - Safety — Safety / EHS / Compliance lead (OSHA, autonomous-equipment
             protocols). More influential in pharma / food & bev / healthcare.

This agent makes NO external calls. It synthesizes the structured claims
already emitted by agents 1–6 into one `INFERRED` claim per persona,
explaining *why this persona will care* and citing — via
`inference_logic` — the prior claim snippet that drove the angle.

V1 implementation is deterministic. A keyword-driven scoring step picks
the strongest one or two prior claims per persona, then formats the
angle. Phase 4 may add an LLM synthesis pass; the structure here is
designed so that drop-in is mechanical.

Contract surface (do not edit upstream):
  - Returns an `AgentResult` with `agent_name="agent_7_department_angles"`.
  - Emits exactly one claim per persona (5 total) when at least one
    upstream claim exists. Each claim is `INFERRED` if a relevant prior
    was found, otherwise `NOT_FOUND` describing the gap.
  - If `prior_results` is empty, or every upstream claim is ERROR /
    NOT_FOUND, emit a single `NOT_FOUND` claim explaining the upstream
    gap — agent 7 itself did not fail.
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

AGENT_NAME = "agent_7_department_angles"
SECTION_TITLE = "Department Angles / Personas"

# Five buyer personas, ordered for stable output. Keys are stable
# identifiers used by the renderer. Labels mirror the framework in
# CLAUDE.md → Buyer Personas.
PERSONA_ORDER: List[str] = ["TDM", "ODM", "FS", "IT", "Safety"]

PERSONA_LABELS: Dict[str, str] = {
    "TDM": "Technical Decision Maker",
    "ODM": "Operational Decision Maker",
    "FS": "Financial Sponsor",
    "IT": "IT Stakeholder",
    "Safety": "Safety / Compliance Stakeholder",
}

# Each persona has a list of (keyword, weight) signals plus a one-line
# "why this persona will care" lens. The lens is concatenated with a
# snippet of the matched prior claim to form the angle text. Weights let
# the strongest signal win when multiple priors apply.
#
# Keep this list tight — the heuristic is intentionally explainable. If
# Phase 4 swaps in an LLM, the lens strings become prompt hints rather
# than format strings.
PersonaSignals = List[Tuple[str, int]]


@dataclass(frozen=True)
class PersonaLens:
    """Per-persona heuristic: keywords to look for + the pain lens."""

    signals: PersonaSignals
    lens: str  # One-line framing of why this persona cares.


PERSONA_LENSES: Dict[str, PersonaLens] = {
    "TDM": PersonaLens(
        signals=[
            ("automation", 3),
            ("robotics", 3),
            ("drone", 3),
            ("autonomous", 3),
            ("wms", 2),
            ("warehouse management", 2),
            ("technology", 2),
            ("upgrade", 2),
            ("modernization", 2),
            ("continuous improvement", 3),
            ("industrial engineering", 3),
            ("digital", 1),
            ("ai ", 1),
            ("integration", 1),
        ],
        lens=(
            "Owns the technical evaluation; this signal lines up with "
            "the automation / CI charter they'd be measured on."
        ),
    ),
    "ODM": PersonaLens(
        signals=[
            ("inventory accuracy", 3),
            ("cycle count", 3),
            ("inventory", 2),
            ("warehouse", 2),
            ("distribution center", 2),
            ("dc opening", 3),
            ("new dc", 3),
            ("facility expansion", 3),
            ("fulfillment", 2),
            ("labor", 2),
            ("hiring", 1),
            ("throughput", 2),
            ("operations", 1),
            ("network", 1),
            ("productivity", 2),
        ],
        lens=(
            "Owns floor-level execution; this is the operational pain "
            "or expansion pressure they'll feel first."
        ),
    ),
    "FS": PersonaLens(
        signals=[
            ("shrink", 3),
            ("write-off", 3),
            ("write off", 3),
            ("inventory loss", 3),
            ("cost reduction", 3),
            ("margin", 2),
            ("capex", 2),
            ("opex", 2),
            ("ebitda", 2),
            ("earnings", 2),
            ("audit", 2),
            ("guidance", 2),
            ("cost", 1),
            ("roi", 2),
            ("savings", 2),
        ],
        lens=(
            "Approves the budget release; this signal frames the "
            "P&L impact the business case has to clear."
        ),
    ),
    "IT": PersonaLens(
        signals=[
            ("wms", 3),
            ("erp", 3),
            ("sap", 2),
            ("manhattan", 2),
            ("blue yonder", 2),
            ("integration", 3),
            ("api", 2),
            ("data", 2),
            ("security", 3),
            ("infrastructure", 2),
            ("connectivity", 2),
            ("wi-fi", 2),
            ("network", 1),
            ("cloud", 1),
            ("sftp", 2),
        ],
        lens=(
            "Gates data flow and integration; this signal tells you "
            "whether IT will fast-track or block deployment."
        ),
    ),
    "Safety": PersonaLens(
        signals=[
            ("osha", 3),
            ("safety", 3),
            ("incident", 3),
            ("injury", 3),
            ("compliance", 3),
            ("audit", 2),
            ("fda", 3),
            ("recall", 2),
            ("ehs", 3),
            ("autonomous", 2),
            ("regulation", 2),
            ("inspection", 2),
            ("pharma", 1),
            ("food", 1),
        ],
        lens=(
            "Signs off on autonomous-equipment protocols; this is the "
            "compliance lens that has to clear before a pilot lands."
        ),
    ),
}

# Hard caps so a misbehaving upstream agent can't blow out a Slack block.
SNIPPET_MAX = 180
LENS_MAX = 220


@dataclass
class AgentContext:
    """Context for the Department Angles agent.

    Unlike most agents this one needs the outputs of agents 1–6, not an
    external API client. The dispatcher fills `prior_results` with the
    completed sibling `AgentResult`s before invoking `run()`.
    """

    account_name: str
    prior_results: List[AgentResult] = field(default_factory=list)
    intent: Optional[str] = None


async def run(ctx: AgentContext) -> AgentResult:
    """Synthesize department-specific angles from prior agent outputs.

    Behavior:
      1. Walk every Claim across `ctx.prior_results`.
      2. Drop ERROR / NOT_FOUND claims — only score "real" findings.
      3. For each persona, pick the highest-scoring 1–2 claims and emit
         one `INFERRED` claim. Snippet from the source claim goes into
         `inference_logic` so the rep can trace the angle.
      4. If no scored claims exist anywhere, emit one `NOT_FOUND` claim
         describing the upstream gap. Otherwise per-persona gaps are
         emitted as their own `NOT_FOUND` claims.

    Never raises. Catches narrowly and logs via `safe_log_exception`.
    """
    started = time.monotonic()

    try:
        usable_claims = _collect_usable_claims(ctx.prior_results or [])

        if not usable_claims:
            # Distinguish "nothing was provided" from "everything failed
            # upstream" only via the text — both collapse to a single
            # NOT_FOUND because there is nothing honest to synthesize.
            reason = _upstream_gap_reason(ctx.prior_results or [])
            claim = Claim(
                text=reason,
                source_tag=SourceTag.NOT_FOUND,
            )
            return AgentResult(
                agent_name=AGENT_NAME,
                section_title=SECTION_TITLE,
                claims=[claim],
                duration_ms=_elapsed_ms(started),
                notes="Upstream agents produced no actionable claims.",
            )

        claims: List[Claim] = []
        for persona_key in PERSONA_ORDER:
            persona_claim = _build_persona_claim(persona_key, usable_claims)
            claims.append(persona_claim)

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
            "[agent_7] department-angles synthesis failed",
        )
        return AgentResult.error(
            AGENT_NAME,
            SECTION_TITLE,
            "Department angles synthesis failed.",
        )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _collect_usable_claims(prior_results: List[AgentResult]) -> List[Claim]:
    """Flatten claims across prior agents, dropping ERROR and NOT_FOUND.

    Both PUBLIC and INFERRED (and INTERNAL) are considered usable for
    synthesis — a sibling agent's inference is still a real signal.
    """
    usable: List[Claim] = []
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
            usable.append(claim)
    return usable


def _upstream_gap_reason(prior_results: List[AgentResult]) -> str:
    """Best-effort one-liner describing why nothing could be synthesized."""
    if not prior_results:
        return (
            "Department angles unavailable — no upstream research was "
            "supplied to synthesize from."
        )
    # If every upstream result is ERROR-only, say so. Otherwise it's
    # just upstream NOT_FOUND coverage.
    has_any_error = any(
        any(c.source_tag is SourceTag.ERROR for c in (r.claims or []))
        for r in prior_results
    )
    has_only_gaps = all(
        all(c.source_tag in (SourceTag.ERROR, SourceTag.NOT_FOUND)
            for c in (r.claims or []))
        for r in prior_results
    )
    if has_any_error and has_only_gaps:
        return (
            "Department angles unavailable — upstream agents returned "
            "only errors or empty results, so there is no signal to "
            "translate into persona-specific pain framing."
        )
    return (
        "Department angles unavailable — upstream agents returned no "
        "sourced findings to translate into persona-specific pain framing."
    )


def _score_claim_for_persona(claim: Claim, lens: PersonaLens) -> int:
    """Sum weighted keyword hits in the claim text. 0 means irrelevant."""
    text = (claim.text or "").lower()
    if not text:
        return 0
    score = 0
    for keyword, weight in lens.signals:
        if keyword in text:
            score += weight
    return score


def _truncate(text: str, cap: int) -> str:
    text = (text or "").strip()
    if len(text) <= cap:
        return text
    return text[: cap - 1].rstrip() + "…"


def _build_persona_claim(
    persona_key: str,
    usable_claims: List[Claim],
) -> Claim:
    """Pick the strongest prior claims for this persona and frame them."""
    lens = PERSONA_LENSES[persona_key]
    label = PERSONA_LABELS[persona_key]

    # Score every usable claim for this persona; keep the top scoring
    # ones (cap at 2 so the rendered output stays terse).
    scored = [
        (claim, _score_claim_for_persona(claim, lens))
        for claim in usable_claims
    ]
    scored = [(c, s) for c, s in scored if s > 0]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    top = scored[:2]

    if not top:
        return Claim(
            text=(
                f"{label} ({persona_key}): no upstream signal mapped to "
                "this persona's pain — flag in discovery."
            ),
            source_tag=SourceTag.NOT_FOUND,
        )

    snippets = [_truncate(c.text, SNIPPET_MAX) for c, _ in top]
    snippet_blob = " | ".join(snippets)
    lens_line = _truncate(lens.lens, LENS_MAX)

    text = (
        f"{label} ({persona_key}) angle: {lens_line} "
        f"Anchor: \"{snippets[0]}\""
    )

    inference_logic = (
        f"Synthesized from {len(top)} upstream claim(s); "
        f"persona lens applied via keyword match. "
        f"Source snippet(s): {snippet_blob}"
    )

    return Claim(
        text=_truncate(text, 480),
        source_tag=SourceTag.INFERRED,
        inference_logic=_truncate(inference_logic, 800),
    )
