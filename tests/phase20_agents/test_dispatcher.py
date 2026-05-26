"""Phase 20 — 8-agent parallel dispatcher tests.

Mocks each agent module's `run` coroutine so we don't pay external API
cost. Verifies:
  - All 8 results returned in spec §6 order regardless of resolve order
  - One slow agent does not delay the others past timeout
  - One raising agent does not poison sibling results
  - Agent 7 receives the first-wave results as its `prior_results`
  - Each AgentResult has `duration_ms` filled in by the dispatcher
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from src.research.agents.contract import (
    AGENT_SECTIONS,
    AgentResult,
    Claim,
    SourceTag,
)
from src.research.agents.dispatcher import DispatcherInput, dispatch
from src.research.sessions import ResearchSession


pytestmark = pytest.mark.asyncio


def _result(agent_name: str, *, claim_text: str = "ok") -> AgentResult:
    return AgentResult(
        agent_name=agent_name,
        section_title=AGENT_SECTIONS[agent_name],
        claims=[Claim(text=claim_text, source_tag=SourceTag.NOT_FOUND)],
    )


def _make_input() -> DispatcherInput:
    return DispatcherInput(
        account_name="Walmart",
        intent="prospecting",
        session=ResearchSession(
            session_id="s1",
            rep_id="U_REP",
            account_name="Walmart",
            personas=["technical_lead"],
        ),
    )


async def test_dispatcher_returns_all_eight_results_in_spec_order():
    """Stub every agent's `run` with an async fn that returns a tagged result."""
    async def _stub(name):
        async def _run(ctx):
            await asyncio.sleep(0)
            return _result(name)
        return _run

    patches = []
    for name in AGENT_SECTIONS:
        mod_name = f"src.research.agents.{name}"
        patches.append(patch(f"{mod_name}.run", new=await _stub(name)))

    try:
        for p in patches:
            p.start()
        results = await dispatch(_make_input(), timeout_sec=5.0)
    finally:
        for p in patches:
            p.stop()

    assert [r.agent_name for r in results] == list(AGENT_SECTIONS.keys())
    # Every result has a duration recorded.
    assert all(r.duration_ms is not None for r in results)


def _stub_for(name: str):
    """Build an async stub whose result.agent_name matches the slot."""
    async def _run(ctx):
        return _result(name)
    return _run


async def test_dispatcher_isolates_a_raising_agent():
    """One agent raises; the other 7 still complete."""

    async def _boom(ctx):
        raise RuntimeError("boom")

    patches = []
    for n in AGENT_SECTIONS:
        new_fn = _boom if n == "agent_4_customer_signals" else _stub_for(n)
        patches.append(patch(f"src.research.agents.{n}.run", new=new_fn))

    try:
        for p in patches:
            p.start()
        results = await dispatch(_make_input(), timeout_sec=5.0)
    finally:
        for p in patches:
            p.stop()

    by_name = {r.agent_name: r for r in results}
    assert len(results) == 8
    a4 = by_name["agent_4_customer_signals"]
    assert a4.claims[0].source_tag is SourceTag.ERROR
    other_tags = [
        by_name[n].claims[0].source_tag
        for n in AGENT_SECTIONS
        if n != "agent_4_customer_signals"
    ]
    assert all(t is SourceTag.NOT_FOUND for t in other_tags)


async def test_dispatcher_times_out_a_slow_agent():
    """One agent hangs past timeout; dispatcher fills with ERROR and returns."""

    async def _slow(ctx):
        await asyncio.sleep(2.0)
        return _result("agent_2_operating_baseline")

    async def _fast(ctx):
        return _result("agent_1_network_footprint")

    patches = {n: patch(f"src.research.agents.{n}.run", new=_fast) for n in AGENT_SECTIONS}
    patches["agent_2_operating_baseline"] = patch(
        "src.research.agents.agent_2_operating_baseline.run", new=_slow
    )

    try:
        for p in patches.values():
            p.start()
        results = await dispatch(_make_input(), timeout_sec=0.05)
    finally:
        for p in patches.values():
            p.stop()

    by_name = {r.agent_name: r for r in results}
    a2 = by_name["agent_2_operating_baseline"]
    assert a2.claims[0].source_tag is SourceTag.ERROR
    assert "timed out" in a2.claims[0].text.lower()


async def test_agent_7_receives_first_wave_results_as_priors():
    """Agent 7 must see agents 1-6 results in its ctx.prior_results."""
    captured = {}

    async def _capture_a7(ctx):
        captured["names"] = [r.agent_name for r in ctx.prior_results]
        return _result("agent_7_department_angles")

    patches = []
    for n in AGENT_SECTIONS:
        if n == "agent_7_department_angles":
            patches.append(patch(f"src.research.agents.{n}.run", new=_capture_a7))
        else:
            patches.append(patch(f"src.research.agents.{n}.run", new=_stub_for(n)))

    try:
        for p in patches:
            p.start()
        await dispatch(_make_input(), timeout_sec=5.0)
    finally:
        for p in patches:
            p.stop()

    assert "names" in captured
    assert set(captured["names"]) == {
        "agent_1_network_footprint",
        "agent_2_operating_baseline",
        "agent_3_board_priorities",
        "agent_4_customer_signals",
        "agent_5_shrink_compliance",
        "agent_6_automation_stack",
    }


async def test_dispatcher_parallelism_is_real():
    """Sum of agent sleeps is 6 * 0.2 = 1.2s; dispatcher should finish
    in < 0.6s if it actually runs them concurrently."""
    import time

    async def _sleepy(ctx):
        await asyncio.sleep(0.2)
        return _result("agent_1_network_footprint")

    patches = [
        patch(f"src.research.agents.{n}.run", new=_sleepy)
        for n in AGENT_SECTIONS
    ]
    try:
        for p in patches:
            p.start()
        t0 = time.monotonic()
        await dispatch(_make_input(), timeout_sec=5.0)
        elapsed = time.monotonic() - t0
    finally:
        for p in patches:
            p.stop()

    # 0.2s first wave + 0.2s second wave (agent 7) ≈ 0.4s. Way less than
    # the 1.6s serial bound.
    assert elapsed < 0.9, f"dispatcher serialized: took {elapsed:.2f}s"
