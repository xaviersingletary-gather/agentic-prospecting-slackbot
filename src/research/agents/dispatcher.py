"""Parallel 8-agent dispatcher (Phase 3 task #6, May 26 V1 spec).

Replaces the 4-topic ThreadPoolExecutor in `findings_builder.py`. Runs
all 8 agents concurrently via `asyncio.gather`, each behind a timeout,
and returns the results in spec §6 order regardless of completion order.

This module is intentionally lightweight — it knows about agent shape
(`AgentContext`, `AgentResult`) but not about Slack rendering or
persistence. Phase 4's aggregator consumes the list this returns.

Trust posture:
- Per-agent timeouts cap wall time so one slow agent (Exa hang, EDGAR
  5xx) can't block the whole research run.
- Per-agent exceptions are caught at the boundary; the failing agent
  yields `AgentResult.error(...)` and the other seven still complete.
- Logs use `safe_log_exception` (CLAUDE.md hygiene).
- Empty/missing inputs are handled by each agent's own validation —
  the dispatcher does not pre-filter.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from src.research.agents.contract import AGENT_SECTIONS, AgentResult
from src.research.sessions import ResearchSession
from src.security.exception_logger import safe_log_exception

# Per-agent imports. Each agent module defines its own AgentContext
# dataclass; we import them under prefixed aliases so the dispatcher
# can build the right context for each.
from src.research.agents import (
    agent_1_network_footprint as a1,
    agent_2_operating_baseline as a2,
    agent_3_board_priorities as a3,
    agent_4_customer_signals as a4,
    agent_5_shrink_compliance as a5,
    agent_6_automation_stack as a6,
    agent_7_department_angles as a7,
    agent_8_contacts as a8,
)

logger = logging.getLogger(__name__)


# Per-agent wall-clock cap (seconds). Each agent must produce SOMETHING
# within this window or the dispatcher converts the slot to an
# AgentResult.error(...). Tuned so the whole run targets <3 min (spec §9
# success criterion).
DEFAULT_AGENT_TIMEOUT_SEC = 45.0

# Order matches AGENT_SECTIONS (spec §6). The aggregator renders in this
# order regardless of which agent finishes first.
_AGENT_ORDER = list(AGENT_SECTIONS.keys())


@dataclass
class DispatcherInput:
    """Everything the 8 agents collectively need.

    Each agent reads only the fields it needs; the dispatcher constructs
    each agent's own context from these. Tests inject mocked clients
    here; production callers leave them None and the dispatcher pulls
    real clients from `clients_factory` (Phase 4).
    """

    account_name: str
    intent: Optional[str] = None
    industry: Optional[str] = None
    session: Optional[ResearchSession] = None
    # External client injections (test path; production = None → factories).
    exa_client: Any = None
    edgar_client: Any = None
    apollo_client: Any = None
    hubspot_contact_client: Any = None
    hubspot_portal_id: Optional[str] = None


def _build_a1(inp: DispatcherInput):
    return a1.AgentContext(
        account_name=inp.account_name,
        industry=inp.industry,
        intent=inp.intent,
        exa_client=inp.exa_client,
    )


def _build_a2(inp: DispatcherInput):
    return a2.AgentContext(
        account_name=inp.account_name,
        intent=inp.intent,
        exa_client=inp.exa_client,
        edgar_client=inp.edgar_client,
    )


def _build_a3(inp: DispatcherInput):
    return a3.AgentContext(
        account_name=inp.account_name,
        exa_client=inp.exa_client,
        intent=inp.intent,
    )


def _build_a4(inp: DispatcherInput):
    return a4.AgentContext(
        account_name=inp.account_name,
        exa_client=inp.exa_client,
        intent=inp.intent,
    )


def _build_a5(inp: DispatcherInput):
    # Agent 5 builds its EDGAR client internally; only exa_client is
    # injectable through ctx. EDGAR test patches go through the module
    # import path inside the agent.
    return a5.AgentContext(
        account_name=inp.account_name,
        industry=inp.industry,
        exa_client=inp.exa_client,
        intent=inp.intent,
    )


def _build_a6(inp: DispatcherInput):
    return a6.AgentContext(
        account_name=inp.account_name,
        exa_client=inp.exa_client,
        intent=inp.intent,
    )


def _build_a8(inp: DispatcherInput):
    return a8.AgentContext(
        session=inp.session,
        apollo_client=inp.apollo_client,
        hubspot_contact_client=inp.hubspot_contact_client,
        hubspot_portal_id=inp.hubspot_portal_id,
    )


# Maps agent_name → (module, context_builder). Agent 7 is special — it
# needs the outputs of agents 1-6, so it's dispatched in a second wave.
_FIRST_WAVE = [
    ("agent_1_network_footprint", a1, _build_a1),
    ("agent_2_operating_baseline", a2, _build_a2),
    ("agent_3_board_priorities", a3, _build_a3),
    ("agent_4_customer_signals", a4, _build_a4),
    ("agent_5_shrink_compliance", a5, _build_a5),
    ("agent_6_automation_stack", a6, _build_a6),
    ("agent_8_contacts", a8, _build_a8),
]


async def _run_one(
    name: str,
    module: Any,
    ctx: Any,
    timeout_sec: float,
) -> AgentResult:
    """Run one agent with timeout + error containment.

    Records `duration_ms` on the returned result so the aggregator can
    surface slow agents in usage logs.
    """
    section_title = AGENT_SECTIONS.get(name, name)
    start = time.monotonic()
    try:
        result = await asyncio.wait_for(module.run(ctx), timeout=timeout_sec)
    except asyncio.TimeoutError:
        logger.warning(
            "[dispatcher] %s timed out after %.1fs", name, timeout_sec
        )
        result = AgentResult.error(
            name, section_title, f"Agent timed out after {int(timeout_sec)}s"
        )
    except Exception as e:  # noqa: BLE001
        safe_log_exception(logger, e, f"[dispatcher] {name} raised")
        result = AgentResult.error(
            name, section_title, "Agent raised an unhandled exception"
        )

    elapsed_ms = int((time.monotonic() - start) * 1000)
    if result.duration_ms is None:
        result.duration_ms = elapsed_ms
    return result


async def dispatch(
    inp: DispatcherInput,
    *,
    timeout_sec: float = DEFAULT_AGENT_TIMEOUT_SEC,
) -> List[AgentResult]:
    """Run all 8 agents and return results in spec §6 order.

    Two waves:
      1. Agents 1-6 + 8 in parallel (no dependencies).
      2. Agent 7 (department angles) — needs prior results to synthesize.

    Returns a list of 8 AgentResults, one per agent slot, in the order
    of `AGENT_SECTIONS`. Failed slots are filled with `AgentResult.error`
    rather than dropped, so the Slack section header is always present.
    """
    # First wave — all independent agents in parallel.
    first_wave_tasks = []
    for name, module, builder in _FIRST_WAVE:
        ctx = builder(inp)
        first_wave_tasks.append(_run_one(name, module, ctx, timeout_sec))

    first_results = await asyncio.gather(*first_wave_tasks)

    # Index by agent_name for second-wave lookup + final ordering.
    by_name: Dict[str, AgentResult] = {r.agent_name: r for r in first_results}

    # Second wave — agent 7 needs the outputs of agents 1-6.
    a7_priors = [
        by_name[name]
        for name in (
            "agent_1_network_footprint",
            "agent_2_operating_baseline",
            "agent_3_board_priorities",
            "agent_4_customer_signals",
            "agent_5_shrink_compliance",
            "agent_6_automation_stack",
        )
        if name in by_name
    ]
    a7_ctx = a7.AgentContext(
        account_name=inp.account_name,
        prior_results=a7_priors,
        intent=inp.intent,
    )
    a7_result = await _run_one(
        "agent_7_department_angles", a7, a7_ctx, timeout_sec
    )
    by_name["agent_7_department_angles"] = a7_result

    # Final ordering — spec §6.
    ordered = [by_name[name] for name in _AGENT_ORDER if name in by_name]
    return ordered
