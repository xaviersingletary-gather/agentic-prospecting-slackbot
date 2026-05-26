"""May 26 V1 research agents.

Eight agents, one module per agent (`agent_N_<short_name>.py`). Each
exports `async def run(ctx: AgentContext) -> AgentResult`. The
dispatcher in `findings_builder.py` imports `AGENT_REGISTRY` from this
package — never the per-agent modules — so parallel subagent worktrees
can ship one at a time without touching shared import order.

Contract:
    from src.research.agents.contract import (
        AgentResult, Claim, SourceTag, AGENT_SECTIONS,
    )
"""
from src.research.agents.contract import (
    AGENT_SECTIONS,
    AgentResult,
    Claim,
    SourceTag,
)

__all__ = ["AGENT_SECTIONS", "AgentResult", "Claim", "SourceTag"]
