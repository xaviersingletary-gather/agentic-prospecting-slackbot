"""Tests for Agent 7 — Department Angles / Personas.

Agent 7 has no external dependencies — it synthesizes the outputs of
agents 1–6 into per-persona pain framing. Tests cover:

  1. Rich-priors (Walmart): four PUBLIC claims spanning shrink,
     automation, growth, and a board priority. Every persona should get
     an INFERRED angle that cites a snippet from the relevant prior.
  2. Sparse-priors (GEODIS): a single PUBLIC claim covering one pain
     area. Personas with no signal must come back as NOT_FOUND; the one
     that does map gets an INFERRED.
  3. Empty priors (Notion, outside-ICP): empty `prior_results=[]` ⇒
     exactly one NOT_FOUND claim explaining the upstream gap.
  4. All-error priors: every upstream claim is ERROR ⇒ agent 7 returns
     a NOT_FOUND, not an ERROR (agent 7 itself did not fail).

Tests also round-trip `to_dict()` to confirm schema validation didn't
silently produce a malformed claim.
"""
from __future__ import annotations

import asyncio
from typing import List

import pytest

from src.research.agents.agent_7_department_angles import (
    AGENT_NAME,
    PERSONA_ORDER,
    SECTION_TITLE,
    AgentContext,
    run,
)
from src.research.agents.contract import AgentResult, Claim, SourceTag


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _public(text: str, url: str = "https://example.com/article") -> Claim:
    return Claim(text=text, source_tag=SourceTag.PUBLIC, source_url=url)


def _inferred(text: str, logic: str = "derived from public data") -> Claim:
    return Claim(text=text, source_tag=SourceTag.INFERRED, inference_logic=logic)


def _error(text: str = "Exa timed out") -> Claim:
    return Claim(text=text, source_tag=SourceTag.ERROR)


def _not_found(text: str = "No public data") -> Claim:
    return Claim(text=text, source_tag=SourceTag.NOT_FOUND)


def _wrap(agent_name: str, section_title: str, claims: List[Claim]) -> AgentResult:
    return AgentResult(
        agent_name=agent_name,
        section_title=section_title,
        claims=claims,
    )


def _run(ctx: AgentContext) -> AgentResult:
    return asyncio.run(run(ctx))


# ---------------------------------------------------------------------------
# 1. Rich priors — Walmart
# ---------------------------------------------------------------------------


def test_rich_priors_emit_one_inferred_per_persona():
    walmart_priors: List[AgentResult] = [
        _wrap(
            "agent_5_shrink_compliance",
            "Shrink & Compliance",
            [
                _public(
                    "Walmart reported $3B in inventory shrink for the fiscal year, "
                    "calling out write-offs as a margin pressure in the latest earnings call.",
                    url="https://example.com/walmart-shrink",
                ),
            ],
        ),
        _wrap(
            "agent_6_automation_stack",
            "Automation Stack",
            [
                _public(
                    "Walmart expanded its Symbotic robotics and warehouse automation "
                    "deployment to 25 regional distribution centers, integrating with the WMS.",
                    url="https://example.com/walmart-automation",
                ),
            ],
        ),
        _wrap(
            "agent_1_network_footprint",
            "Network Footprint",
            [
                _public(
                    "Walmart announced four new DC openings and a multi-year "
                    "facility expansion plan to grow fulfillment throughput.",
                    url="https://example.com/walmart-dc",
                ),
            ],
        ),
        _wrap(
            "agent_3_board_priorities",
            "Board Priorities",
            [
                _public(
                    "Walmart's board flagged OSHA safety and compliance audits as a "
                    "top operational priority after a recent incident at a regional facility.",
                    url="https://example.com/walmart-board",
                ),
            ],
        ),
    ]

    ctx = AgentContext(account_name="Walmart", prior_results=walmart_priors)
    result = _run(ctx)

    # Header / shape
    assert result.agent_name == AGENT_NAME
    assert result.section_title == SECTION_TITLE
    assert result.duration_ms is not None and result.duration_ms >= 0
    assert len(result.claims) == len(PERSONA_ORDER) == 5

    # All five personas present, in the documented order.
    for idx, persona_key in enumerate(PERSONA_ORDER):
        claim = result.claims[idx]
        assert persona_key in claim.text, (
            f"Claim {idx} should mention persona key {persona_key!r}: {claim.text!r}"
        )

    # Rich priors should produce INFERRED across the board (no gaps).
    for claim in result.claims:
        assert claim.source_tag is SourceTag.INFERRED, (
            f"Expected INFERRED, got {claim.source_tag} for {claim.text!r}"
        )
        assert claim.inference_logic, "INFERRED claims must carry inference_logic"

    # to_dict() must round-trip every claim (no validator caught upstream).
    payload = result.to_dict()
    assert len(payload["claims"]) == 5
    for entry in payload["claims"]:
        assert entry["source_tag"] == "inferred"

    # Spot-check: the persona angle should cite a snippet from a relevant
    # prior. FS = shrink; TDM/IT = automation; ODM = growth; Safety = audit.
    by_persona = {p: c for p, c in zip(PERSONA_ORDER, result.claims)}
    assert "shrink" in by_persona["FS"].inference_logic.lower()
    assert (
        "automation" in by_persona["TDM"].inference_logic.lower()
        or "robotics" in by_persona["TDM"].inference_logic.lower()
    )
    assert (
        "dc" in by_persona["ODM"].inference_logic.lower()
        or "fulfillment" in by_persona["ODM"].inference_logic.lower()
        or "expansion" in by_persona["ODM"].inference_logic.lower()
    )
    assert (
        "osha" in by_persona["Safety"].inference_logic.lower()
        or "safety" in by_persona["Safety"].inference_logic.lower()
        or "compliance" in by_persona["Safety"].inference_logic.lower()
    )
    # IT should pull from automation/WMS signal.
    assert (
        "wms" in by_persona["IT"].inference_logic.lower()
        or "integrat" in by_persona["IT"].inference_logic.lower()
    )


# ---------------------------------------------------------------------------
# 2. Sparse priors — GEODIS
# ---------------------------------------------------------------------------


def test_sparse_priors_emit_not_found_for_personas_without_signal():
    geodis_priors: List[AgentResult] = [
        _wrap(
            "agent_5_shrink_compliance",
            "Shrink & Compliance",
            [
                _public(
                    "GEODIS disclosed an internal cost reduction initiative tied to "
                    "reducing inventory shrink across its US 3PL network.",
                    url="https://example.com/geodis-shrink",
                ),
            ],
        ),
        # The remaining agents found nothing — counts as good output.
        _wrap(
            "agent_6_automation_stack",
            "Automation Stack",
            [_not_found("No automation deployments disclosed.")],
        ),
        _wrap(
            "agent_3_board_priorities",
            "Board Priorities",
            [_not_found("No board materials surfaced.")],
        ),
    ]

    ctx = AgentContext(account_name="GEODIS", prior_results=geodis_priors)
    result = _run(ctx)

    assert len(result.claims) == 5
    by_persona = {p: c for p, c in zip(PERSONA_ORDER, result.claims)}

    # FS should win the shrink-driven angle.
    assert by_persona["FS"].source_tag is SourceTag.INFERRED
    assert "shrink" in by_persona["FS"].inference_logic.lower()

    # The other personas have no direct signal — they should NOT_FOUND.
    # (ODM may pick up "inventory" as a weak signal; we keep this loose
    # by counting how many personas came back as NOT_FOUND.)
    not_found_count = sum(
        1 for c in result.claims if c.source_tag is SourceTag.NOT_FOUND
    )
    inferred_count = sum(
        1 for c in result.claims if c.source_tag is SourceTag.INFERRED
    )
    assert not_found_count >= 2, (
        f"Expected most personas to NOT_FOUND on sparse priors; "
        f"got {not_found_count} NOT_FOUND, {inferred_count} INFERRED"
    )
    assert inferred_count >= 1

    # NOT_FOUND claims must validate via to_dict round-trip.
    payload = result.to_dict()
    assert len(payload["claims"]) == 5


# ---------------------------------------------------------------------------
# 3. Empty priors — Notion (outside-ICP control)
# ---------------------------------------------------------------------------


def test_empty_priors_emit_single_not_found():
    ctx = AgentContext(account_name="Notion", prior_results=[])
    result = _run(ctx)

    assert result.agent_name == AGENT_NAME
    assert result.section_title == SECTION_TITLE
    assert len(result.claims) == 1
    only_claim = result.claims[0]
    assert only_claim.source_tag is SourceTag.NOT_FOUND
    assert "no upstream research" in only_claim.text.lower() or \
        "department angles unavailable" in only_claim.text.lower()
    # round-trip
    assert result.to_dict()["claims"][0]["source_tag"] == "not_found"


# ---------------------------------------------------------------------------
# 4. All-error priors
# ---------------------------------------------------------------------------


def test_all_error_priors_emit_not_found_not_error():
    error_priors: List[AgentResult] = [
        _wrap(
            "agent_1_network_footprint",
            "Network Footprint",
            [_error("Exa timed out")],
        ),
        _wrap(
            "agent_5_shrink_compliance",
            "Shrink & Compliance",
            [_error("EDGAR 5xx")],
        ),
    ]
    ctx = AgentContext(account_name="GenericCo", prior_results=error_priors)
    result = _run(ctx)

    # Agent 7 itself did not fail — must NOT_FOUND, not ERROR.
    assert len(result.claims) == 1
    only_claim = result.claims[0]
    assert only_claim.source_tag is SourceTag.NOT_FOUND, (
        f"Expected NOT_FOUND on all-error upstream, got {only_claim.source_tag}"
    )
    # Agent name and section still rendered so the rep sees the gap.
    assert result.agent_name == AGENT_NAME
    assert result.section_title == SECTION_TITLE


# ---------------------------------------------------------------------------
# Schema round-trip — every persona claim must satisfy Claim.__post_init__
# ---------------------------------------------------------------------------


def test_every_emitted_claim_round_trips_to_dict():
    """Schema check: ensure every output Claim is constructable + serializable."""
    priors: List[AgentResult] = [
        _wrap(
            "agent_5_shrink_compliance",
            "Shrink & Compliance",
            [_public("Inventory shrink reduction is a board priority.")],
        ),
        _wrap(
            "agent_6_automation_stack",
            "Automation Stack",
            [_public("Pilot automation deployment in a regional DC.")],
        ),
    ]
    ctx = AgentContext(account_name="Roundtrip Co", prior_results=priors)
    result = _run(ctx)

    payload = result.to_dict()
    assert payload["agent_name"] == AGENT_NAME
    assert payload["section_title"] == SECTION_TITLE
    for entry in payload["claims"]:
        # Always present, never null.
        assert "text" in entry and entry["text"]
        assert entry["source_tag"] in {"inferred", "not_found"}
        if entry["source_tag"] == "inferred":
            assert entry.get("inference_logic"), (
                "INFERRED claim missing inference_logic in serialized output"
            )


def test_handles_malformed_prior_results_gracefully():
    """Defense in depth: a None or non-AgentResult entry should not blow up."""
    priors = [
        None,  # noqa: type-arg — runtime garbage
        _wrap(
            "agent_5_shrink_compliance",
            "Shrink & Compliance",
            [_public("Walmart shrink event hit margin guidance.")],
        ),
    ]
    # mypy will complain; the agent is supposed to be defensive at runtime.
    ctx = AgentContext(account_name="Walmart", prior_results=priors)  # type: ignore[arg-type]
    result = _run(ctx)
    assert result.agent_name == AGENT_NAME
    # Should still produce 5 persona claims (at least one INFERRED).
    assert len(result.claims) == 5
    assert any(c.source_tag is SourceTag.INFERRED for c in result.claims)


def test_intent_field_is_accepted_but_not_required():
    ctx = AgentContext(account_name="X", prior_results=[], intent="qualify_account")
    result = _run(ctx)
    assert result.agent_name == AGENT_NAME
    assert len(result.claims) == 1
    assert result.claims[0].source_tag is SourceTag.NOT_FOUND
