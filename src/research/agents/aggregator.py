"""Assemble dispatcher output into a persisted research blob.

Takes the list of AgentResults the dispatcher returns plus the
account metadata, produces the JSON shape that lands in
`AccountResearch.research_blob`. This is the canonical representation
of a single research run — Phase 5's follow-up Q&A reads from the
same blob.

Pure transformation. No I/O, no Slack, no DB writes. The caller
(runner) handles persistence.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.research.agents.contract import AGENT_SECTIONS, AgentResult


def assemble_research_blob(
    *,
    account_name: str,
    intent: Optional[str],
    agent_results: List[AgentResult],
    timestamp: Optional[datetime] = None,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the persisted research blob.

    Shape (consumed by both Phase 4 renderer and Phase 5 Q&A):

        {
          "schema_version": 1,
          "account_name": "Walmart",
          "intent": "prospecting",
          "generated_at": "2026-05-26T14:00:00+00:00",
          "agents": [
            {
              "agent_name": "agent_1_network_footprint",
              "section_title": "Network Footprint",
              "claims": [{"text": "...", "source_tag": "public", ...}],
              "duration_ms": 1234,
              "notes": null,
            },
            ...  # one entry per AGENT_SECTIONS slot, in spec §6 order
          ],
          "notes": ...,
          "stats": {
            "total_claims": 23,
            "claims_by_tag": {"public": 14, "inferred": 3, "not_found": 5, "error": 1},
            "errored_agents": ["agent_5_shrink_compliance"],
          }
        }
    """
    ts = timestamp or datetime.now(timezone.utc)
    by_name = {r.agent_name: r for r in agent_results}

    agents_payload: List[Dict[str, Any]] = []
    for name in AGENT_SECTIONS:
        r = by_name.get(name)
        if r is None:
            # No result for this slot — synthesize a NOT_FOUND placeholder
            # so downstream renderers always have the section header.
            agents_payload.append(
                AgentResult.error(
                    name, AGENT_SECTIONS[name], "Agent did not run."
                ).to_dict()
            )
        else:
            agents_payload.append(r.to_dict())

    stats = _compute_stats(agent_results)

    blob: Dict[str, Any] = {
        "schema_version": 1,
        "account_name": account_name,
        "intent": intent,
        "generated_at": ts.isoformat(),
        "agents": agents_payload,
        "stats": stats,
    }
    if notes:
        blob["notes"] = notes
    return blob


def _compute_stats(agent_results: List[AgentResult]) -> Dict[str, Any]:
    by_tag: Dict[str, int] = {}
    errored: List[str] = []
    total = 0
    for r in agent_results:
        if any(c.source_tag.value == "error" for c in r.claims):
            errored.append(r.agent_name)
        for c in r.claims:
            total += 1
            by_tag[c.source_tag.value] = by_tag.get(c.source_tag.value, 0) + 1
    return {
        "total_claims": total,
        "claims_by_tag": by_tag,
        "errored_agents": errored,
    }


def format_timestamp_for_slack(ts_iso: str) -> str:
    """Convert an ISO-8601 timestamp string into the Slack header format
    the spec calls for: 'Research pulled May 26, 2026 at 2:14pm ET'.

    Returns a graceful fallback ("Research pulled <ts>") if parsing
    fails — the timestamp is metadata, never a blocker.
    """
    try:
        dt = datetime.fromisoformat(ts_iso)
    except (ValueError, TypeError):
        return f"Research pulled {ts_iso}"

    # Convert to Eastern; rep team is US-based per CLAUDE.md.
    try:
        from zoneinfo import ZoneInfo

        et = dt.astimezone(ZoneInfo("America/New_York"))
    except Exception:  # noqa: BLE001 — tz lookup can fail in stripped envs
        et = dt

    # %-I drops the leading zero on hour ("2" not "02"); macOS/Linux only.
    try:
        time_part = et.strftime("%-I:%M%p").lower()
    except ValueError:
        # Windows fallback: %#I — but Windows isn't a target. Keep it simple.
        time_part = et.strftime("%I:%M%p").lstrip("0").lower()
    # "2:14pm" → "2:14pm ET"
    date_part = et.strftime("%B %d, %Y").replace(" 0", " ")  # drop "May 06"→"May 6"
    return f"Research pulled {date_part} at {time_part} ET"
