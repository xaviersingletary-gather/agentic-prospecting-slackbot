"""Agent 10 — Hook Candidates + Why Now.

Hybrid agent. First runs a deterministic picker that selects 2–3 dated,
sourced trigger events from prior `AgentResult`s emitted by agents 1–6.
That picker is the grounding layer. THEN for each picked trigger it
runs a short LLM synthesis pass (`synthesize_with_fallback`) that turns
the raw claim into an actual first-line of cold-outreach copy a rep
could send. A single LLM call also drafts the "Why Now" urgency frame.

Spec context (Gather AI SDR cold-outreach skill):

  > Write 2 to 3 specific, sourced openers Steve could use in the first
  > line of his email. Each one must anchor to something real — a news
  > item, a trigger event, an operational detail, or something from the
  > LinkedIn PDF if provided.
  >
  > Hook candidates are raw material, not finished lines. Steve will
  > refine them. Label the source for each one so he can verify it.
  >
  > Do not write hooks that could apply to any logistics company. If you
  > cannot find something specific, say so and write "HOOK TBD — no
  > strong signal found."

The AE morning brief expects a "Why Now" line: 2–3 sentences with the
specific trigger event framed for outreach urgency.

Trust posture:
  - Synthesis is text-in / text-out, no tools wired (see synthesis.py).
  - When the LLM is unavailable (no key, timeout, parse fail) the agent
    falls back to a deterministic template so the rep never sees an
    empty section.

Contract surface (do not edit upstream):
  - Returns an `AgentResult` with
    `agent_name="agent_10_hook_candidates"`.
  - Emits up to 3 hook claims (PUBLIC when grounded in a dated source,
    NOT_FOUND when a slot can't be filled) followed by exactly one
    "Why Now" claim (INFERRED when a trigger exists, NOT_FOUND when no
    dated signal is available).
  - `industry_pain_result` is read for vocabulary only — its claims are
    NOT eligible to become hooks themselves.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from src.research.agents.contract import (
    AgentResult,
    Claim,
    SourceTag,
)
from src.research.agents.synthesis import (
    build_grounding_stanza,
    synthesize_with_fallback,
)
from src.security.exception_logger import safe_log_exception

logger = logging.getLogger(__name__)

AGENT_NAME = "agent_10_hook_candidates"
SECTION_TITLE = "Hook Candidates + Why Now"

# Hard caps so a misbehaving upstream agent can't blow out a Slack block.
HOOK_TEXT_MAX = 320
WHY_NOW_TEXT_MAX = 720
SNIPPET_MAX = 180

# Spec: up to 3 hook slots, fill the rest with HOOK TBD when no strong
# signal is found in the last 12 months.
MAX_HOOKS = 3
MIN_HOOKS = 2  # Below this we still pad to MAX_HOOKS with NOT_FOUND.

# 12-month freshness window for ranked signals.
FRESHNESS_WINDOW_DAYS = 365

HOOK_TBD_TEXT = (
    "HOOK TBD — no strong dated signal found in last 12 months."
)

# SDR-skill rule we embed verbatim in every hook prompt. Quoted upstream
# in the worker prompt; do not paraphrase — the explicit rule is what
# keeps the LLM honest. Tests assert on this literal string.
GENERIC_OPENER_BAN = (
    "Do not write hooks that could apply to any logistics company."
)

# Priority order of upstream sections — higher = stronger trigger.
# Mapping is keyed by canonical section title (case-insensitive match)
# so we tolerate small wording drift.
#
#   1 (highest) Shrink & Compliance
#   2           Automation Stack
#   3           Board Priorities
#   4           Customer-Facing Signals
#   5           Operating Baseline
SECTION_PRIORITY: Dict[str, int] = {
    "shrink & compliance": 1,
    "shrink and compliance": 1,
    "automation stack": 2,
    "board priorities": 3,
    "customer-facing signals": 4,
    "customer facing signals": 4,
    "operating baseline": 5,
}

# Lower number = higher priority. Anything not in the map is treated as
# the weakest tier so its claims can still be picked when nothing else
# qualifies, but it always loses ties.
DEFAULT_PRIORITY = 99


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------


@dataclass
class AgentContext:
    """Context for the Hook Candidates agent.

    `prior_results` is the list of completed sibling `AgentResult`s
    (agents 1–6) the dispatcher fills in before invoking `run()`.
    `industry_pain_result` (Agent 9) is read for vocabulary only; its
    claims are NOT eligible to become hooks themselves.
    """

    account_name: str
    prior_results: List[AgentResult] = field(default_factory=list)
    industry_pain_result: Optional[AgentResult] = None
    intent: Optional[str] = None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def run(ctx: AgentContext) -> AgentResult:
    """Synthesize hook candidates + a Why Now line from prior outputs.

    Behavior:
      1. Walk every Claim across `ctx.prior_results` (NOT including
         `industry_pain_result`).
      2. Keep only claims with BOTH `source_url` AND a `date` that
         parses to a calendar date within the last 12 months.
      3. Rank surviving claims by section priority (shrink > automation
         > board > customer-facing > operating baseline), breaking ties
         by recency (newer wins).
      4. Pick the top 2–3, deduplicating by source_url. For each pick,
         run a short LLM synthesis pass to turn the raw claim into an
         actual first-line cold-outreach opener. Emit one PUBLIC hook
         per pick. Pad remaining slots with NOT_FOUND "HOOK TBD" claims
         so the rendered output always shows 3 candidate slots.
      5. Emit one Why Now claim:
         - INFERRED when at least one dated trigger exists; framed by a
           second LLM call off the highest-priority + freshest claim.
         - NOT_FOUND when no dated trigger exists (LLM is NOT called).

    Never raises. Catches narrowly and logs via `safe_log_exception`.
    """
    started = time.monotonic()

    try:
        priors = ctx.prior_results or []
        dated = _collect_dated_claims(priors)

        ranked = _rank_claims(dated)
        picks = _dedupe_by_url(ranked, MAX_HOOKS)

        claims: List[Claim] = []

        # Hook synthesis — one LLM call per picked trigger. Each pick is
        # already grounded in a real source_url + date; the LLM only
        # rewrites the claim text into a first-line opener.
        for parent_result, claim, _section_prio, _claim_date in picks:
            claims.append(
                _build_synthesized_hook_claim(
                    account_name=ctx.account_name,
                    parent=parent_result,
                    claim=claim,
                )
            )

        # Pad remaining slots with HOOK TBD up to MAX_HOOKS so the rep
        # always sees 3 slots, even when only 1 strong signal exists.
        while len(claims) < MAX_HOOKS:
            claims.append(
                Claim(
                    text=HOOK_TBD_TEXT,
                    source_tag=SourceTag.NOT_FOUND,
                )
            )

        # Why Now — single LLM call framing off the strongest ranked
        # claim. If no ranked claims exist we emit NOT_FOUND instead and
        # DO NOT call the LLM (saves spend on a deterministic answer).
        if picks:
            claims.append(
                _build_synthesized_why_now_claim(
                    account_name=ctx.account_name,
                    picks=picks,
                )
            )
        else:
            claims.append(
                Claim(
                    text=(
                        "Why Now unavailable — no dated trigger event "
                        "found in the last 12 months across upstream "
                        "research. Flag in discovery."
                    ),
                    source_tag=SourceTag.NOT_FOUND,
                )
            )

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
            "[agent_10] hook-candidates synthesis failed",
        )
        return AgentResult.error(
            AGENT_NAME,
            SECTION_TITLE,
            "Hook candidates synthesis failed.",
        )


# ---------------------------------------------------------------------------
# Internals — collection & ranking
# ---------------------------------------------------------------------------


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


# Tuple shape: (parent_result, claim, section_priority, parsed_date)
RankedClaim = Tuple[AgentResult, Claim, int, datetime]


def _collect_dated_claims(priors: List[AgentResult]) -> List[RankedClaim]:
    """Flatten priors → list of (parent, claim, section_priority, date).

    Only claims with a `source_url` AND a parseable `date` within the
    last 12 months survive. ERROR / NOT_FOUND claims are dropped (no
    real signal). INFERRED claims survive iff they carry both fields —
    rare but legitimate (an upstream inference grounded in a sourced
    quote with a date).
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=FRESHNESS_WINDOW_DAYS)

    survivors: List[RankedClaim] = []
    for result in priors:
        if not isinstance(result, AgentResult):
            continue
        prio = _section_priority(result.section_title or "")
        for claim in result.claims or []:
            if not isinstance(claim, Claim):
                continue
            if claim.source_tag in (SourceTag.ERROR, SourceTag.NOT_FOUND):
                continue
            if not claim.source_url:
                continue
            if not claim.date:
                continue
            parsed = _parse_date_lenient(claim.date)
            if parsed is None:
                continue
            # Cap at "now" so a malformed future date can't dominate
            # ranking, and require it falls within the freshness window.
            if parsed > now:
                continue
            if parsed < cutoff:
                continue
            survivors.append((result, claim, prio, parsed))
    return survivors


def _section_priority(section_title: str) -> int:
    """Map a section title to its hook-ranking priority (lower = higher).

    Match is case-insensitive and tolerates the two written variants
    (& vs and). Unknown sections fall through to DEFAULT_PRIORITY so
    they only get picked when nothing better exists.
    """
    key = (section_title or "").strip().lower()
    return SECTION_PRIORITY.get(key, DEFAULT_PRIORITY)


_ISO_DATE_RE = re.compile(r"(\d{4})-(\d{1,2})(?:-(\d{1,2}))?")
_YEAR_QUARTER_RE = re.compile(r"q([1-4])\s*(\d{4})", re.IGNORECASE)


def _parse_date_lenient(raw: str) -> Optional[datetime]:
    """Parse a date string leniently. Returns None on failure.

    Handles:
      - "YYYY-MM-DD"
      - "YYYY-MM" → treat as the 1st of the month
      - "YYYY"   → treat as Jan 1
      - "Q3 2025" / "q3 2025" → 1st day of the quarter
      - Free text containing an ISO-ish substring (e.g.
        "Reported 2025-10-12 in 10-Q").

    All returned datetimes are tz-aware (UTC) so comparison against the
    freshness cutoff is total.
    """
    if not raw or not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None

    # Quarter form first — easy short-circuit.
    qm = _YEAR_QUARTER_RE.search(text)
    if qm:
        try:
            q = int(qm.group(1))
            y = int(qm.group(2))
            month = {1: 1, 2: 4, 3: 7, 4: 10}[q]
            return datetime(y, month, 1, tzinfo=timezone.utc)
        except (KeyError, ValueError):
            pass

    m = _ISO_DATE_RE.search(text)
    if m:
        try:
            y = int(m.group(1))
            mo = int(m.group(2)) if m.group(2) else 1
            d = int(m.group(3)) if m.group(3) else 1
            mo = max(1, min(mo, 12))
            d = max(1, min(d, 28))  # keep days safe for any month
            return datetime(y, mo, d, tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return None

    # Bare 4-digit year as last resort.
    bare = re.search(r"\b(20\d{2})\b", text)
    if bare:
        try:
            return datetime(int(bare.group(1)), 1, 1, tzinfo=timezone.utc)
        except ValueError:
            return None

    return None


def _rank_claims(dated: List[RankedClaim]) -> List[RankedClaim]:
    """Sort by (section_priority asc, date desc).

    Lower section_priority = higher importance. Within the same
    section bucket, newer claims win.
    """
    return sorted(dated, key=lambda t: (t[2], -t[3].timestamp()))


def _dedupe_by_url(
    ranked: List[RankedClaim], limit: int
) -> List[RankedClaim]:
    """Take the top `limit` claims, dropping any whose source_url has
    already been picked. Preserves rank order."""
    seen: set[str] = set()
    out: List[RankedClaim] = []
    for entry in ranked:
        _, claim, _, _ = entry
        url = claim.source_url or ""
        if url and url in seen:
            continue
        seen.add(url)
        out.append(entry)
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# Internals — formatting + synthesis
# ---------------------------------------------------------------------------


def _truncate(text: str, cap: int) -> str:
    text = (text or "").strip()
    if len(text) <= cap:
        return text
    return text[: cap - 1].rstrip() + "…"


def _summarize_claim(claim_text: str) -> str:
    """Return a tight snippet of the underlying claim text.

    Strips leading bullet artifacts and trailing whitespace, then caps
    at SNIPPET_MAX. The snippet is what the rep will read when deciding
    whether the angle is real.
    """
    if not claim_text:
        return ""
    # Strip common leading markers ("- ", "* ", "• ") and trim.
    text = re.sub(r"^[\-\*•]+\s*", "", claim_text).strip()
    return _truncate(text, SNIPPET_MAX)


def _deterministic_hook_text(parent: AgentResult, claim: Claim) -> str:
    """Fallback hook text used when the LLM call fails.

    Mirrors the pre-LLM behavior so a synthesis outage still yields the
    same shape of hook the rep would have seen before this change.
    """
    snippet = _summarize_claim(claim.text or "")
    section = (parent.section_title or "").strip() or "upstream signal"
    return f"Hook: {snippet} (anchor: {section})"


def _hook_system_prompt() -> str:
    """System prompt for a single hook synthesis call.

    Quotes the SDR skill anti-generic rule verbatim and prepends the
    project-wide grounding stanza (no fabrication, no em-dashes,
    forbidden vocabulary list).
    """
    grounding = build_grounding_stanza(
        role_description=(
            "You are an experienced Gather AI SDR writing one first-line "
            "of cold-outreach copy. The line must reference a specific "
            "fact from the trigger below — never a generic statement that "
            "could apply to any logistics company."
        )
    )
    return (
        f"{grounding}\n"
        "Task: write ONE first-line opener (1-2 sentences) Steve could send "
        "today. The line must:\n"
        f"- Reference at least one specific fact from the trigger "
        "(date, dollar amount, named exec, vendor, facility count, etc.).\n"
        "- Sound like a peer-to-peer note, not a marketing blurb.\n"
        "- Be 1-2 sentences only — no greeting, no signoff, no call-to-action.\n\n"
        f"Hard rule from the SDR skill: {GENERIC_OPENER_BAN} If the trigger "
        "is too thin to write a specific line, return the trigger snippet "
        "unchanged so the rep can refine it manually.\n\n"
        "Return ONLY the opener text. No quotes, no preface, no labels."
    )


def _hook_user_payload(
    *,
    account_name: str,
    parent: AgentResult,
    claim: Claim,
) -> str:
    """User payload for a single hook synthesis call.

    Format matches the worker prompt verbatim so the LLM sees the trust
    boundary clearly: account, trigger origin, the claim text, and the
    source URL the rep can click to verify.
    """
    section_title = (parent.section_title or "upstream signal").strip()
    date = claim.date or "undated"
    return (
        f"Account: {account_name}\n"
        f"Trigger (from {section_title}, dated {date}):\n"
        f"\"{claim.text or ''}\"\n"
        f"Source: {claim.source_url or '(no url)'}"
    )


def _build_synthesized_hook_claim(
    *,
    account_name: str,
    parent: AgentResult,
    claim: Claim,
) -> Claim:
    """Build a PUBLIC hook claim — LLM-synthesized prose, deterministic fallback.

    Format on success: "<1-2 sentence opener written for the rep>".
    Format on fallback: "Hook: {snippet} (anchor: {section})".
    Either way the claim carries the underlying source_url + date so
    the rep can verify the grounding.
    """
    fallback = _deterministic_hook_text(parent, claim)

    try:
        synthesized = synthesize_with_fallback(
            system_prompt=_hook_system_prompt(),
            user_payload=_hook_user_payload(
                account_name=account_name,
                parent=parent,
                claim=claim,
            ),
            fallback=fallback,
            log_label="[agent_10][hook]",
        )
    except Exception as e:  # noqa: BLE001 — defensive; synthesis never raises
        safe_log_exception(
            logger,
            e,
            "[agent_10] hook synthesis raised unexpectedly",
        )
        synthesized = fallback

    text = synthesized or fallback
    return Claim(
        text=_truncate(text, HOOK_TEXT_MAX),
        source_tag=SourceTag.PUBLIC,
        source_url=claim.source_url,
        date=claim.date,
    )


def _deterministic_why_now_text(picks: List[RankedClaim]) -> str:
    """Fallback Why Now text used when the LLM call fails.

    Mirrors the pre-LLM 2-sentence template so a synthesis outage still
    yields the same shape the rep would have seen before this change.
    """
    primary_parent, primary_claim, _, primary_date = picks[0]
    primary_snippet = _summarize_claim(primary_claim.text or "")
    primary_section = (primary_parent.section_title or "").strip() or "upstream signal"
    primary_date_str = primary_claim.date or primary_date.strftime("%Y-%m")

    sentences = [
        (
            f"Why Now: As of {primary_date_str}, "
            f"{primary_section} surfaced a trigger — "
            f"\"{primary_snippet}\"."
        ),
        (
            "This is a fresh, dated signal — they are actively shopping "
            "the problem space, not theoretically interested."
        ),
    ]

    for parent, claim, _, _ in picks[1:]:
        other_section = (parent.section_title or "").strip()
        if other_section and other_section != primary_section:
            second_snippet = _summarize_claim(claim.text or "")
            sentences.append(
                f"Combined with {other_section} ({second_snippet}), "
                "this is an active automation window."
            )
            break

    return " ".join(sentences)


def _why_now_system_prompt() -> str:
    """System prompt for the Why Now synthesis call.

    Same grounding stanza as the hook prompt, plus a Why-Now-specific
    framing brief. The same SDR-skill anti-generic rule applies — a
    Why-Now line that could fit any logistics account is also a fail.
    """
    grounding = build_grounding_stanza(
        role_description=(
            "You are an experienced Gather AI AE writing a 2-3 sentence "
            "Why-Now urgency line for a target account. The line frames "
            "WHY this specific account is worth reaching today, anchored "
            "in a specific dated trigger from the upstream research below."
        )
    )
    return (
        f"{grounding}\n"
        "Task: write 2-3 sentences of persuasive urgency the AE could "
        "paste into a deal-brief. The line must:\n"
        "- Reference the specific dated trigger and its section of origin.\n"
        "- Make the urgency feel specific to THIS account, not generic.\n"
        "- Read like an experienced AE briefing a colleague, not a pitch.\n\n"
        f"Hard rule from the SDR skill: {GENERIC_OPENER_BAN} The Why-Now "
        "line should also not generalize to any logistics company.\n\n"
        "Return ONLY the 2-3 sentence Why Now line. No labels, no preface."
    )


def _why_now_user_payload(
    *,
    account_name: str,
    picks: List[RankedClaim],
) -> str:
    """User payload for the Why Now synthesis call.

    Lists the primary trigger (highest-priority + freshest) and any
    compounding triggers from different sections, so the LLM has the
    same context the deterministic builder used.
    """
    primary_parent, primary_claim, _, _ = picks[0]
    primary_section = (primary_parent.section_title or "upstream signal").strip()
    primary_date = primary_claim.date or "undated"

    lines = [
        f"Account: {account_name}",
        "",
        f"Primary trigger (from {primary_section}, dated {primary_date}):",
        f"\"{primary_claim.text or ''}\"",
        f"Source: {primary_claim.source_url or '(no url)'}",
    ]

    # Compounding triggers from other sections strengthen the frame.
    seen_sections = {primary_section}
    for parent, claim, _, _ in picks[1:]:
        section = (parent.section_title or "").strip()
        if not section or section in seen_sections:
            continue
        seen_sections.add(section)
        lines.extend([
            "",
            f"Compounding trigger (from {section}, dated {claim.date or 'undated'}):",
            f"\"{claim.text or ''}\"",
            f"Source: {claim.source_url or '(no url)'}",
        ])

    return "\n".join(lines)


def _build_synthesized_why_now_claim(
    *,
    account_name: str,
    picks: List[RankedClaim],
) -> Claim:
    """Synthesize a 2–3 sentence Why Now framing via LLM, with a
    deterministic fallback template.

    `inference_logic` lists the upstream section names that contributed
    so the rep can trace the framing.
    """
    primary_parent, primary_claim, _, primary_date = picks[0]
    primary_section = (primary_parent.section_title or "").strip() or "upstream signal"

    contributing_sections = [primary_section]
    for parent, _, _, _ in picks[1:]:
        other_section = (parent.section_title or "").strip()
        if other_section and other_section not in contributing_sections:
            contributing_sections.append(other_section)
            break  # only one compounding section in the inference trace

    fallback = _deterministic_why_now_text(picks)

    try:
        synthesized = synthesize_with_fallback(
            system_prompt=_why_now_system_prompt(),
            user_payload=_why_now_user_payload(
                account_name=account_name,
                picks=picks,
            ),
            fallback=fallback,
            log_label="[agent_10][why_now]",
        )
    except Exception as e:  # noqa: BLE001 — defensive; synthesis never raises
        safe_log_exception(
            logger,
            e,
            "[agent_10] why-now synthesis raised unexpectedly",
        )
        synthesized = fallback

    text = synthesized or fallback

    inference_logic = (
        "Why Now framing derived from upstream sections: "
        + ", ".join(contributing_sections)
        + ". Highest-priority + freshest dated claim was chosen as the "
        "primary trigger."
    )

    return Claim(
        text=_truncate(text, WHY_NOW_TEXT_MAX),
        source_tag=SourceTag.INFERRED,
        inference_logic=_truncate(inference_logic, 800),
        source_url=primary_claim.source_url,
        date=primary_claim.date,
    )
