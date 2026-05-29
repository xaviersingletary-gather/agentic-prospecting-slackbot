"""Agent 6 — Automation Stack (May 26 V1 spec §6).

Pulls WMS, LMS, ERP, robotics, and vision-deployment signals. The spec
notes "Job postings (best signal — tech mentioned in JDs)" — we scope
our Exa queries accordingly: one job-board-flavored query, one press /
case-study query.

Also runs a named-vendor sweep against the SDR cold-outreach skill's
greenfield checklist (Corvus / Verity / Gather AI for drones, plus
Zebra / Impinj / Surgere / TruCount / 6 River Systems for vision-on-MHE
deployments, and Locus / Symbotic as general warehouse robotics). The
sweep produces two leading INFERRED summary claims that answer the
binary YES / NO / UNKNOWN questions an SDR needs before writing email:
  - Drone-based inventory verification: YES / NO / UNKNOWN
  - Vision-on-MHE: YES / NO / UNKNOWN

Wraps existing infrastructure (Exa) into the new contract. The legacy
`findings_builder.py` `automation_vendors` topic remains until task #6
retires it.

Trust posture:
- Every URL passes `assert_safe_url` before storage.
- Logs use `type(e).__name__` only via `safe_log_exception`.
- Snippets are treated as untrusted text — never fed into a tool call.
- Agent never raises; partial / total failure produces a graceful
  AgentResult instead.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple

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

# Vendor sweep config (May 29 — SDR greenfield Y/N checklist).
# Gather AI sits at the front so the current-customer flag has guaranteed
# coverage even with the hard cap. The remaining 9 entries are the SDR
# skill's explicit competitor list.
_VENDOR_SWEEP_LIST: List[str] = [
    "Gather AI",
    "Corvus Robotics",
    "Verity",
    "6 River Systems",
    "Locus Robotics",
    "Symbotic",
    "Impinj",
    "Zebra Technologies",
    "TruCount",
    "Surgere",
]
_MAX_VENDOR_QUERIES = 9          # Cap to save Exa cost.
_CONSECUTIVE_EMPTY_STOP = 3      # Early-stop after 3 zero-result queries.
_VENDOR_SNIPPET_TRUNCATE = 280   # Spec-mandated snippet cap.

# Which vendors map to which Y/N bucket. Gather AI is a current-customer
# flag but ALSO drone-positive (their own deployment = drone evidence).
_DRONE_VENDORS = {"Corvus Robotics", "Verity", "Gather AI"}
_VISION_VENDORS = {
    "6 River Systems",
    "Impinj",
    "Zebra Technologies",
    "Surgere",
    "TruCount",
}


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


def _vendor_claim_from_hit(vendor: str, hit: dict) -> Optional[Claim]:
    """Build a PUBLIC vendor-sweep claim formatted per spec.

    text = ``"{vendor} deployment signal: {snippet truncated to 280 chars}"``
    """
    url = (hit.get("url") or "").strip()
    title = (hit.get("title") or "").strip()
    snippet = (hit.get("snippet") or "").strip()
    if not url:
        return None
    raw = snippet or title
    if not raw:
        return None
    try:
        assert_safe_url(url)
    except BlockedUrlError:
        return None

    if len(raw) > _VENDOR_SNIPPET_TRUNCATE:
        raw = raw[: _VENDOR_SNIPPET_TRUNCATE - 3] + "..."

    text = f"{vendor} deployment signal: {raw}"
    return Claim(
        text=text,
        source_tag=SourceTag.PUBLIC,
        source_url=url,
        date=(hit.get("published_date") or None),
    )


def _run_vendor_sweep(
    company: str,
    exa: ExaSearchClient,
) -> Tuple[List[Claim], set, int]:
    """Sweep the SDR-skill vendor list, capped at _MAX_VENDOR_QUERIES.

    Returns (vendor_claims, matched_vendors, queries_attempted). Early-stop
    triggers after _CONSECUTIVE_EMPTY_STOP zero-result queries in a row to
    keep Exa cost bounded. Errors on individual queries are swallowed
    (logged via safe_log_exception) and counted as "no hit" for sweep
    purposes — they do not abort the loop.
    """
    vendor_claims: List[Claim] = []
    matched: set = set()
    consecutive_empty = 0
    attempted = 0

    for vendor in _VENDOR_SWEEP_LIST:
        if attempted >= _MAX_VENDOR_QUERIES:
            break
        if consecutive_empty >= _CONSECUTIVE_EMPTY_STOP:
            break

        query = f"{company} {vendor} deployment partnership case study"
        attempted += 1
        try:
            hits = exa.search(query, num_results=_EXA_RESULTS_PER_QUERY)
        except Exception as e:  # noqa: BLE001
            safe_log_exception(
                logger, e, f"{AGENT_NAME} vendor sweep exa.search failed"
            )
            consecutive_empty += 1
            continue

        if not hits:
            consecutive_empty += 1
            continue

        emitted_for_vendor = False
        for hit in hits:
            claim = _vendor_claim_from_hit(vendor, hit)
            if claim is None:
                continue
            vendor_claims.append(claim)
            emitted_for_vendor = True

        if emitted_for_vendor:
            matched.add(vendor)
            consecutive_empty = 0
        else:
            # Hits returned but all URL-rejected — still counts as empty
            # for early-stop purposes (no usable signal).
            consecutive_empty += 1

    return vendor_claims, matched, attempted


def _summary_claim(
    label: str,
    answer: str,
    matched_vendor: Optional[str],
    vendor_bucket: str,
) -> Claim:
    """Build a single INFERRED summary claim for the leading-claims slot."""
    if matched_vendor:
        reason = f"matched {matched_vendor}"
    else:
        reason = "no signal"
    text = f"{label}: {answer} — {reason}."
    inference_logic = (
        f"Sweep against Gather AI competitor vendor list "
        f"({vendor_bucket}); reasoning: {reason}."
    )
    return Claim(
        text=text,
        source_tag=SourceTag.INFERRED,
        inference_logic=inference_logic,
    )


def _build_summary_claims(matched_vendors: set) -> List[Claim]:
    """Produce the two leading INFERRED claims (drones + vision)."""
    drone_matches = matched_vendors & _DRONE_VENDORS
    vision_matches = matched_vendors & _VISION_VENDORS

    drone_answer = "YES" if drone_matches else "UNKNOWN"
    vision_answer = "YES" if vision_matches else "UNKNOWN"

    # Pick a representative matched vendor for the one-line reason. Sort
    # so the choice is deterministic for tests.
    drone_vendor = sorted(drone_matches)[0] if drone_matches else None
    vision_vendor = sorted(vision_matches)[0] if vision_matches else None

    drone_claim = _summary_claim(
        "Drone-based inventory verification",
        drone_answer,
        drone_vendor,
        "Corvus / Verity / Gather AI",
    )
    vision_claim = _summary_claim(
        "Vision-on-MHE",
        vision_answer,
        vision_vendor,
        "Zebra / Impinj / Surgere / TruCount / 6 River Systems",
    )
    return [drone_claim, vision_claim]


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

    # --- Named-vendor sweep (May 29 SDR greenfield checklist) -----------
    # Runs after the core queries so the existing automation-stack claims
    # are unaffected. The two INFERRED summary claims are inserted at the
    # front of the section so the SDR sees the binary Y/N first.
    try:
        vendor_claims, matched_vendors, _ = _run_vendor_sweep(company, exa)
    except Exception as e:  # noqa: BLE001 — sweep must never abort the agent.
        safe_log_exception(logger, e, f"{AGENT_NAME} vendor sweep crashed")
        vendor_claims, matched_vendors = [], set()

    # Append vendor PUBLIC claims after the existing automation-stack
    # claims so reviewers see WMS/robotics signals first, then competitor
    # deployment signals.
    claims.extend(vendor_claims)

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

    # Insert the two leading INFERRED summary claims so they render first.
    summary_claims = _build_summary_claims(matched_vendors)
    for sc in reversed(summary_claims):
        claims.insert(0, sc)

    return AgentResult(
        agent_name=AGENT_NAME,
        section_title=SECTION_TITLE,
        claims=claims,
    )
