"""Phase 20 — Agent 6 (Automation Stack) tests.

External Exa client is mocked. Covers:
  - PUBLIC claims when sourceable (core WMS / robotics queries)
  - NOT_FOUND when all queries return empty
  - ERROR when every Exa query raises
  - Unsafe URLs silently dropped
  - Empty account name short-circuits with ERROR
  - Named-vendor sweep — drone YES via Corvus, vision YES via Zebra
  - Both summary claims UNKNOWN when nothing surfaces (GEODIS / Notion)
  - Two leading INFERRED summary claims emitted in correct order
  - Existing WMS / Symbotic claims preserved after the summary block
  - Schema round-trip through Claim.to_dict()
  - Vendor-sweep early-stop after N consecutive empty queries
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.research.agents.agent_6_automation_stack import (
    _CONSECUTIVE_EMPTY_STOP,
    _MAX_VENDOR_QUERIES,
    AgentContext,
    run,
)
from src.research.agents.contract import Claim, SourceTag


pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hit(*, url, title="", snippet="", date=None):
    return {
        "url": url,
        "title": title,
        "snippet": snippet,
        "published_date": date,
    }


def _make_exa(*, core_responses, vendor_responses_by_query=None,
              default_vendor_response=None):
    """Build a mocked Exa client with deterministic behavior.

    core_responses: list[list[dict]] — one per scoped query (3 queries).
    vendor_responses_by_query: dict[str_substring -> list[dict]] — match
        against the query text so test authors don't have to know the
        vendor sweep order.
    default_vendor_response: list[dict] returned for vendor queries that
        don't match a key. Defaults to [].
    """
    if default_vendor_response is None:
        default_vendor_response = []
    vendor_responses_by_query = vendor_responses_by_query or {}
    core_iter = iter(core_responses)

    def _search(query, num_results=8):
        # Core queries are the first three; identify them by the keyword
        # signature each builds with.
        if (
            "job posting WMS" in query
            or "warehouse robotics deployment" in query
            or "case study automation success" in query
        ):
            try:
                return next(core_iter)
            except StopIteration:
                return []
        for needle, payload in vendor_responses_by_query.items():
            if needle in query:
                return payload
        return default_vendor_response

    mock = MagicMock()
    mock.search.side_effect = _search
    return mock


# ---------------------------------------------------------------------------
# Core automation-stack behavior (carried forward from earlier coverage)
# ---------------------------------------------------------------------------


async def test_walmart_emits_public_claims_for_job_postings_and_vendors():
    """Existing automation claims preserved AFTER the two summary claims."""
    fake_exa = _make_exa(
        core_responses=[
            [
                _hit(
                    url="https://walmart.com/careers/wms-engineer-bentonville",
                    title="WMS Engineer — Walmart Distribution",
                    snippet=(
                        "Manage Blue Yonder WMS deployments across the "
                        "Walmart distribution network."
                    ),
                )
            ],
            [
                _hit(
                    url="https://logistics.com/walmart-symbotic-rollout",
                    title="Walmart accelerates Symbotic robotics rollout",
                    snippet="Walmart expands Symbotic deployment to 25 DCs.",
                    date="2025-04-10",
                )
            ],
            [],
        ],
        vendor_responses_by_query={
            # Symbotic vendor-sweep hit — vendor sweep still works.
            "Symbotic": [
                _hit(
                    url="https://logistics.com/walmart-symbotic-2025",
                    snippet="Walmart expands Symbotic robotics partnership.",
                )
            ],
        },
    )

    result = await run(
        AgentContext(account_name="Walmart", exa_client=fake_exa)
    )

    # Two leading INFERRED summary claims first.
    assert result.claims[0].source_tag is SourceTag.INFERRED
    assert result.claims[1].source_tag is SourceTag.INFERRED
    assert "Drone-based inventory verification" in result.claims[0].text
    assert "Vision-on-MHE" in result.claims[1].text

    public_claims = [
        c for c in result.claims if c.source_tag is SourceTag.PUBLIC
    ]
    # Both the existing WMS + Symbotic claims survive.
    assert any(
        "Blue Yonder" in c.text or "WMS" in c.text for c in public_claims
    )
    assert any(
        "Symbotic" in c.text and "deployment signal:" not in c.text
        for c in public_claims
    ), "Original Symbotic case-study claim should survive untouched"


async def test_geodis_private_has_partial_signal_unknown_sweep():
    """GEODIS-style — one core hit, no vendor matches; both summaries UNKNOWN."""
    fake_exa = _make_exa(
        core_responses=[
            [
                _hit(
                    url="https://geodis.com/careers/automation-lead",
                    title="Automation Lead — GEODIS",
                    snippet=(
                        "Lead automation initiatives across GEODIS facilities."
                    ),
                )
            ],
            [],
            [],
        ],
        default_vendor_response=[],
    )
    result = await run(
        AgentContext(account_name="GEODIS", exa_client=fake_exa)
    )
    drone, vision = result.claims[0], result.claims[1]
    assert drone.source_tag is SourceTag.INFERRED
    assert vision.source_tag is SourceTag.INFERRED
    assert "UNKNOWN" in drone.text
    assert "UNKNOWN" in vision.text
    assert "no signal" in drone.text
    assert "no signal" in vision.text
    assert "no signal" in (drone.inference_logic or "")
    assert "no signal" in (vision.inference_logic or "")

    # Core WMS/automation claim still preserved.
    public = [c for c in result.claims if c.source_tag is SourceTag.PUBLIC]
    assert len(public) == 1
    assert "GEODIS" in public[0].text


async def test_notion_outside_icp_emits_not_found_and_unknown_sweep():
    """Outside-ICP: no false positives anywhere."""
    fake_exa = _make_exa(
        core_responses=[[], [], []],
        default_vendor_response=[],
    )

    result = await run(
        AgentContext(account_name="Notion", exa_client=fake_exa)
    )

    # Leading two are the INFERRED Y/N summaries.
    assert result.claims[0].source_tag is SourceTag.INFERRED
    assert result.claims[1].source_tag is SourceTag.INFERRED
    assert "UNKNOWN" in result.claims[0].text
    assert "UNKNOWN" in result.claims[1].text

    # Then a single NOT_FOUND claim for the core queries.
    tail = result.claims[2:]
    assert len(tail) == 1
    assert tail[0].source_tag is SourceTag.NOT_FOUND
    # No PUBLIC false positives.
    assert not any(c.source_tag is SourceTag.PUBLIC for c in result.claims)


async def test_total_exa_failure_returns_error(caplog):
    """All core queries raising → ERROR result, vendor sweep skipped."""
    fake_exa = MagicMock()
    fake_exa.search.side_effect = RuntimeError("upstream down")
    with caplog.at_level("ERROR"):
        result = await run(
            AgentContext(account_name="Walmart", exa_client=fake_exa)
        )

    assert len(result.claims) == 1
    assert result.claims[0].source_tag is SourceTag.ERROR
    log_text = "\n".join(r.message for r in caplog.records)
    assert "RuntimeError" in log_text
    assert "upstream down" not in log_text


async def test_unsafe_url_dropped_silently():
    fake_exa = _make_exa(
        core_responses=[
            [
                _hit(
                    url="http://10.0.0.1/internal", title="bad", snippet="x",
                ),
                _hit(
                    url="https://walmart.com/careers/wms",
                    title="WMS Engineer",
                    snippet="Blue Yonder WMS owner",
                ),
            ],
            [],
            [],
        ],
    )
    result = await run(
        AgentContext(account_name="Walmart", exa_client=fake_exa)
    )
    public = [c for c in result.claims if c.source_tag is SourceTag.PUBLIC]
    assert all(
        "10.0.0.1" not in (c.source_url or "") for c in public
    )


async def test_empty_account_name_returns_error():
    fake_exa = MagicMock()
    result = await run(AgentContext(account_name="", exa_client=fake_exa))
    assert len(result.claims) == 1
    assert result.claims[0].source_tag is SourceTag.ERROR
    fake_exa.search.assert_not_called()


# ---------------------------------------------------------------------------
# Named-vendor sweep — required cases per task brief
# ---------------------------------------------------------------------------


async def test_walmart_style_drone_yes_via_corvus():
    """Corvus query returns a hit → Drone claim YES, references Corvus."""
    fake_exa = _make_exa(
        core_responses=[[], [], []],
        vendor_responses_by_query={
            "Corvus Robotics": [
                _hit(
                    url="https://corvus.com/walmart-deploy",
                    title="Walmart Corvus drone deployment",
                    snippet=(
                        "Walmart pilots Corvus Robotics autonomous drones "
                        "for inventory cycle counts across 4 DCs."
                    ),
                ),
            ],
        },
    )
    result = await run(
        AgentContext(account_name="Walmart", exa_client=fake_exa)
    )

    drone, vision = result.claims[0], result.claims[1]
    assert drone.source_tag is SourceTag.INFERRED
    assert "YES" in drone.text
    assert "Corvus Robotics" in drone.text
    assert "Corvus Robotics" in (drone.inference_logic or "")

    # Vision still UNKNOWN — no matching vendor.
    assert "UNKNOWN" in vision.text

    # PUBLIC vendor-sweep claim emitted with spec-mandated text format.
    public = [c for c in result.claims if c.source_tag is SourceTag.PUBLIC]
    assert any(
        c.text.startswith("Corvus Robotics deployment signal:")
        for c in public
    )


async def test_vision_yes_via_zebra_forklift_camera():
    """Zebra query returns a forklift-camera deployment → Vision YES.

    We seed an unrelated Locus Robotics hit to keep the sweep alive past
    the 3-consecutive-empty early-stop threshold so Zebra is reached.
    """
    # Seed an early-vendor hit (Corvus) so the 3-consecutive-empty
    # early-stop doesn't trip before Zebra (8th in the sweep order). Test
    # focus is Vision YES via Zebra; drone status is incidental here.
    fake_exa = _make_exa(
        core_responses=[[], [], []],
        vendor_responses_by_query={
            "Corvus Robotics": [
                _hit(
                    url="https://corvus.com/acme-pilot",
                    snippet="Acme pilots Corvus drones at one DC.",
                )
            ],
            # Keep the sweep alive — Locus + Symbotic keep the consecutive
            # empty counter from tripping before Zebra (8th).
            "Locus Robotics": [
                _hit(
                    url="https://logistics.com/acme-locus",
                    snippet="Acme deploys Locus AMRs.",
                )
            ],
            "Symbotic": [
                _hit(
                    url="https://example.com/acme-symbotic",
                    snippet="Acme runs Symbotic in another facility.",
                )
            ],
            "Zebra Technologies": [
                _hit(
                    url="https://zebra.com/forklift-vision-case",
                    title="Zebra forklift-mounted vision at Acme",
                    snippet=(
                        "Acme Logistics deploys Zebra forklift-camera "
                        "system for automated pallet scanning and LPN "
                        "capture across all 12 DCs."
                    ),
                )
            ],
        },
    )
    result = await run(
        AgentContext(account_name="Acme Logistics", exa_client=fake_exa)
    )

    drone, vision = result.claims[0], result.claims[1]
    # Vision-on-MHE is the assertion target.
    assert vision.source_tag is SourceTag.INFERRED
    assert "YES" in vision.text
    assert "Zebra Technologies" in vision.text
    assert "Zebra Technologies" in (vision.inference_logic or "")
    # Drone also resolves to YES given the Corvus seed (sanity check).
    assert drone.source_tag is SourceTag.INFERRED

    public = [c for c in result.claims if c.source_tag is SourceTag.PUBLIC]
    assert any(
        c.text.startswith("Zebra Technologies deployment signal:")
        for c in public
    )


async def test_gather_ai_match_flags_drone_yes():
    """If Gather AI surfaces, drone Y/N = YES (current customer)."""
    fake_exa = _make_exa(
        core_responses=[[], [], []],
        vendor_responses_by_query={
            "Gather AI": [
                _hit(
                    url="https://gather.ai/case/acme",
                    title="Acme deploys Gather AI",
                    snippet=(
                        "Acme Logistics expands Gather AI drone-based "
                        "inventory across 8 distribution centers."
                    ),
                )
            ],
        },
    )
    result = await run(
        AgentContext(account_name="Acme Logistics", exa_client=fake_exa)
    )
    drone = result.claims[0]
    assert "YES" in drone.text
    assert "Gather AI" in drone.text


async def test_vendor_snippet_truncated_to_280():
    """Snippets longer than 280 chars are truncated."""
    long_snip = "x" * 500
    fake_exa = _make_exa(
        core_responses=[[], [], []],
        vendor_responses_by_query={
            "Corvus Robotics": [
                _hit(
                    url="https://corvus.com/walmart",
                    snippet=long_snip,
                ),
            ],
        },
    )
    result = await run(
        AgentContext(account_name="Walmart", exa_client=fake_exa)
    )
    sweep_claims = [
        c for c in result.claims
        if c.source_tag is SourceTag.PUBLIC
        and c.text.startswith("Corvus Robotics deployment signal:")
    ]
    assert sweep_claims, "Expected a Corvus sweep claim"
    # Truncated snippet portion <= 280 chars.
    snippet_portion = sweep_claims[0].text.split(
        "Corvus Robotics deployment signal: ", 1
    )[1]
    assert len(snippet_portion) <= 280


async def test_vendor_sweep_early_stops_after_consecutive_empties():
    """After 3 consecutive zero-result queries, sweep aborts."""
    calls = []

    def _search(query, num_results=8):
        calls.append(query)
        # Core queries return empty so we get into the sweep deterministically.
        if (
            "job posting WMS" in query
            or "warehouse robotics deployment" in query
            or "case study automation success" in query
        ):
            return []
        # All vendor queries return empty.
        return []

    mock = MagicMock()
    mock.search.side_effect = _search

    await run(AgentContext(account_name="QuietCo", exa_client=mock))

    vendor_calls = [
        c for c in calls if "deployment partnership case study" in c
    ]
    # Should NOT have queried the full _MAX_VENDOR_QUERIES — early stop kicks in.
    assert len(vendor_calls) <= _CONSECUTIVE_EMPTY_STOP
    assert len(vendor_calls) <= _MAX_VENDOR_QUERIES


async def test_vendor_sweep_capped_at_9_queries():
    """Sweep never exceeds _MAX_VENDOR_QUERIES (9), even with all hits."""
    calls = []

    def _search(query, num_results=8):
        calls.append(query)
        if (
            "job posting WMS" in query
            or "warehouse robotics deployment" in query
            or "case study automation success" in query
        ):
            return []
        # Every vendor query returns a hit so consecutive-empty never trips.
        return [
            _hit(
                url=f"https://example.com/{len(calls)}",
                snippet="generic deployment signal",
            )
        ]

    mock = MagicMock()
    mock.search.side_effect = _search

    await run(AgentContext(account_name="EverythingCo", exa_client=mock))

    vendor_calls = [
        c for c in calls if "deployment partnership case study" in c
    ]
    assert len(vendor_calls) == _MAX_VENDOR_QUERIES == 9


async def test_summary_claims_roundtrip_to_dict():
    """Schema round-trip: summary claims validate and serialize cleanly."""
    fake_exa = _make_exa(
        core_responses=[[], [], []],
        vendor_responses_by_query={
            "Corvus Robotics": [
                _hit(
                    url="https://corvus.com/x",
                    snippet="Corvus drone deployment at MegaCo.",
                ),
            ],
            "Zebra Technologies": [
                _hit(
                    url="https://zebra.com/x",
                    snippet="Zebra forklift-camera at MegaCo.",
                ),
            ],
        },
    )
    result = await run(
        AgentContext(account_name="MegaCo", exa_client=fake_exa)
    )
    drone, vision = result.claims[0], result.claims[1]

    # __post_init__ already passed (object exists); also confirm round-trip.
    drone_dict = drone.to_dict()
    vision_dict = vision.to_dict()
    assert drone_dict["source_tag"] == "inferred"
    assert vision_dict["source_tag"] == "inferred"
    assert "inference_logic" in drone_dict
    assert "inference_logic" in vision_dict
    assert "Drone-based inventory verification" in drone_dict["text"]
    assert "Vision-on-MHE" in vision_dict["text"]

    # Reconstruct from dict to confirm a clean round-trip.
    Claim(
        text=drone_dict["text"],
        source_tag=drone_dict["source_tag"],
        inference_logic=drone_dict["inference_logic"],
    )
    Claim(
        text=vision_dict["text"],
        source_tag=vision_dict["source_tag"],
        inference_logic=vision_dict["inference_logic"],
    )


async def test_existing_wms_and_symbotic_claims_preserved_after_summary():
    """Required case 6: WMS + Symbotic core claims survive intact after the
    two leading summary claims."""
    fake_exa = _make_exa(
        core_responses=[
            [
                _hit(
                    url="https://target.com/jobs/wms-architect",
                    title="WMS Architect — Target",
                    snippet="Owns Manhattan WMS rollout across 30 DCs.",
                )
            ],
            [
                _hit(
                    url="https://press.com/target-symbotic-pilot",
                    title="Target pilots Symbotic at 3 DCs",
                    snippet=(
                        "Target announces Symbotic robotics pilot across "
                        "3 regional distribution centers."
                    ),
                )
            ],
            [],
        ],
        default_vendor_response=[],
    )
    result = await run(
        AgentContext(account_name="Target", exa_client=fake_exa)
    )

    # First two are summary INFERRED claims.
    assert result.claims[0].source_tag is SourceTag.INFERRED
    assert result.claims[1].source_tag is SourceTag.INFERRED

    # Existing PUBLIC core claims survive in the remainder.
    tail = result.claims[2:]
    wms_hit = [c for c in tail if "Manhattan" in c.text]
    symbotic_hit = [
        c for c in tail
        if "Symbotic" in c.text and not c.text.startswith(
            "Symbotic deployment signal:"
        )
    ]
    assert wms_hit, "WMS core claim should still be present"
    assert symbotic_hit, "Symbotic core press-claim should still be present"
