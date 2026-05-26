"""Phase 22 — v2 followup context builder + system stanza tightening.

Pure-Python (no I/O). Pins the prompt shape so changes are intentional
and the hallucination-guard stanza covers the spec §7 'not in research'
behavior.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.agents.followup_agent import (
    _SYSTEM_STANZA,
    build_followup_context_v2,
)
from src.research.agents.aggregator import assemble_research_blob
from src.research.agents.contract import (
    AGENT_SECTIONS,
    AgentResult,
    Claim,
    SourceTag,
)


def _blob(intent="prospecting"):
    return assemble_research_blob(
        account_name="Walmart",
        intent=intent,
        agent_results=[
            AgentResult(
                agent_name="agent_1_network_footprint",
                section_title=AGENT_SECTIONS["agent_1_network_footprint"],
                claims=[
                    Claim(
                        text="Operates 211 DCs in NA",
                        source_tag=SourceTag.PUBLIC,
                        source_url="https://corp.walmart.com/network",
                    )
                ],
            ),
            AgentResult(
                agent_name="agent_3_board_priorities",
                section_title=AGENT_SECTIONS["agent_3_board_priorities"],
                claims=[
                    Claim(
                        text="Inventory shrink reduction is a top exec priority for FY25.",
                        source_tag=SourceTag.PUBLIC,
                        source_url="https://corp.walmart.com/earnings",
                        date="2025-05-15",
                    )
                ],
            ),
        ],
        timestamp=datetime(2026, 5, 26, 18, 14, tzinfo=timezone.utc),
    )


# ---------------------------------------------------------------------------
# System stanza
# ---------------------------------------------------------------------------


def test_system_stanza_mentions_not_in_research_and_offer_to_add():
    """Spec §7 'not in research' behavior must be in the system stanza."""
    assert "I don't have that in the research" in _SYSTEM_STANZA
    assert "offer to add it" in _SYSTEM_STANZA
    assert "Do not invent facts" in _SYSTEM_STANZA


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------


def test_v2_context_includes_account_blob_question_and_history():
    turns = [
        {"role": "user", "message": "what about DCs?"},
        {"role": "assistant", "message": "211 in NA per corp page."},
    ]
    context = build_followup_context_v2(
        account_name="Walmart",
        research_blob=_blob(),
        turns=turns,
        question="What about board priorities?",
    )
    assert "Walmart" in context
    assert "211 DCs" in context
    assert "shrink" in context.lower()
    assert "what about DCs?" in context
    assert "211 in NA" in context
    assert "Current question:" in context
    assert "What about board priorities?" in context
    # Source tag chips visible in the blob section.
    assert "[public]" in context


def test_v2_context_handles_missing_blob_gracefully():
    context = build_followup_context_v2(
        account_name="Acme",
        research_blob=None,
        turns=None,
        question="anything?",
    )
    assert "Acme" in context
    assert "research still in progress" in context
    assert "Thread so far" in context


def test_v2_context_handles_dataclass_like_turn_rows():
    """The handler passes SQLAlchemy ConversationTurn rows, not dicts."""

    class _Row:
        def __init__(self, role, message):
            self.role = role
            self.message = message

    turns = [_Row("user", "u1"), _Row("assistant", "a1")]
    context = build_followup_context_v2(
        account_name="X",
        research_blob=_blob(),
        turns=turns,
        question="q",
    )
    assert "[user] u1" in context
    assert "[assistant] a1" in context


def test_v2_context_trims_oldest_turns_when_over_budget():
    long_msg = "x" * 4000  # 4 KB per turn → 25 turns ≈ 100 KB
    turns = [{"role": "user", "message": long_msg} for _ in range(25)]
    context = build_followup_context_v2(
        account_name="X",
        research_blob=_blob(),
        turns=turns,
        question="q",
    )
    # Truncation strategy keeps last 10 turns; raw msg appears at most 10x.
    # We don't assert on absolute byte limits — just that we didn't include
    # all 25 verbatim.
    occurrences = context.count(long_msg)
    assert occurrences <= 10


def test_v2_context_clips_blob_when_still_over_budget_after_turn_trim():
    huge_text = "y" * 60_000  # forces blob clip path
    blob = assemble_research_blob(
        account_name="X",
        intent=None,
        agent_results=[
            AgentResult(
                agent_name="agent_1_network_footprint",
                section_title=AGENT_SECTIONS["agent_1_network_footprint"],
                claims=[Claim(text=huge_text, source_tag=SourceTag.NOT_FOUND)],
            )
        ],
    )
    context = build_followup_context_v2(
        account_name="X",
        research_blob=blob,
        turns=None,
        question="q",
    )
    # Either it fits under cap (because no truncation was actually needed),
    # or the truncation marker appears.
    if len(context) > 28_000:
        assert "research truncated" in context
