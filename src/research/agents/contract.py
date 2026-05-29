"""Agent contract — the shape every May 26 V1 research agent returns.

Eight agents (Network Footprint, Operating Baseline, Board Priorities,
Customer Signals, Shrink & Compliance, Automation Stack, Department
Angles, Contacts) each implement `async def run(...) -> AgentResult`.
The dispatcher in `findings_builder.py` runs them in parallel and the
aggregator in `output_formatter.py` renders their `claims` into the
Slack research blob.

Everything in this module is pure data + tiny helpers. No I/O, no
network, no DB. Keeping the contract dependency-free lets agent builders
import it without dragging in Exa / Apollo / EDGAR side effects.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class SourceTag(str, Enum):
    """The spec's five source-attribution categories.

    Every Claim carries exactly one. The aggregator renders them as
    inline chips on the Slack output: ``[public]``, ``[inferred]``, etc.
    """

    PUBLIC = "public"        # Direct quote / link from a public source.
    INFERRED = "inferred"    # Derived from public data + benchmarks.
    INTERNAL = "internal"    # From HubSpot / Gong / internal records.
    NOT_FOUND = "not_found"  # Agent searched, found nothing sourceable.
    ERROR = "error"          # Agent failed; section unavailable.


@dataclass
class Claim:
    """A single tagged finding inside an AgentResult.

    Field requirements depend on `source_tag`:
      - PUBLIC:    `text` + `source_url` required.
      - INFERRED:  `text` + `inference_logic` required; source_url optional.
      - INTERNAL:  `text` + `source` (e.g. "HubSpot", "Gong call 2026-04-12").
      - NOT_FOUND: `text` describes what was searched; everything else None.
      - ERROR:     `text` is the brief failure reason.
    """

    text: str
    source_tag: SourceTag
    source_url: Optional[str] = None
    inference_logic: Optional[str] = None
    source: Optional[str] = None  # Free-form label for INTERNAL claims.
    date: Optional[str] = None    # ISO-ish (YYYY-MM or YYYY-MM-DD); display only.

    def __post_init__(self) -> None:
        # Coerce string into enum so callers can pass either.
        if isinstance(self.source_tag, str):
            self.source_tag = SourceTag(self.source_tag)
        if not self.text or not isinstance(self.text, str):
            raise ValueError("Claim.text must be a non-empty string")
        tag = self.source_tag
        if tag is SourceTag.PUBLIC and not self.source_url:
            raise ValueError("PUBLIC claim requires source_url")
        if tag is SourceTag.INFERRED and not self.inference_logic:
            raise ValueError("INFERRED claim requires inference_logic")
        if tag is SourceTag.INTERNAL and not self.source:
            raise ValueError("INTERNAL claim requires source")

    def to_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        out["source_tag"] = self.source_tag.value
        return {k: v for k, v in out.items() if v is not None}


@dataclass
class AgentResult:
    """One agent's contribution to the research blob.

    `claims` is the list rendered into the Slack section. `agent_name`
    is the spec-stable identifier (e.g. ``"agent_1_network_footprint"``)
    the aggregator uses to look up the section header and ordering.
    `duration_ms` is informational — the dispatcher fills it in if the
    agent didn't.

    Successful agents may still emit a NOT_FOUND claim; that's expected.
    An ERROR claim with `source_tag=ERROR` is reserved for total failure
    (timeout, unhandled exception). When the agent only had a partial
    failure, mix PUBLIC / NOT_FOUND / ERROR claims freely.
    """

    agent_name: str
    section_title: str
    claims: List[Claim] = field(default_factory=list)
    duration_ms: Optional[int] = None
    notes: Optional[str] = None  # Free-form. Surfaced in research_gaps only.

    @classmethod
    def error(
        cls,
        agent_name: str,
        section_title: str,
        reason: str,
    ) -> "AgentResult":
        """Build a single-ERROR-claim result so a failing agent still
        shows up in the assembled blob with its section label."""
        return cls(
            agent_name=agent_name,
            section_title=section_title,
            claims=[Claim(text=reason, source_tag=SourceTag.ERROR)],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "section_title": self.section_title,
            "claims": [c.to_dict() for c in self.claims],
            "duration_ms": self.duration_ms,
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# Registry — wired up phase-by-phase. The dispatcher imports AGENT_REGISTRY
# (NOT the individual modules) so the parallel subagent worktrees can land
# one at a time without touching this file's import order.
# ---------------------------------------------------------------------------

# Maps spec-stable agent_name → human section title. Order is preserved when
# the aggregator renders sections; matches the spec §6 order plus the two
# May 29 additions from the SDR + AE skills audit (agent_9 = Industry & Pain;
# agent_10 = Hook Candidates + Why Now).
AGENT_SECTIONS: Dict[str, str] = {
    "agent_1_network_footprint": "Network Footprint",
    "agent_2_operating_baseline": "Operating Baseline",
    "agent_3_board_priorities": "Board Priorities",
    "agent_4_customer_signals": "Customer-Facing Signals",
    "agent_5_shrink_compliance": "Shrink & Compliance",
    "agent_6_automation_stack": "Automation Stack",
    "agent_9_industry_pain": "Industry & Pain",
    "agent_7_department_angles": "Department Angles / Personas",
    "agent_8_contacts": "Contacts",
    "agent_10_hook_candidates": "Hooks & Why Now",
}
