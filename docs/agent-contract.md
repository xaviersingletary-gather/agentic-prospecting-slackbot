# V1 Research Agent Contract (May 26 spec)

This is the brief every Phase 3 subagent builds against. Five subagents
run in parallel worktrees: agents 1, 2, 4, 5, 7. Each owns one new file
under `src/research/agents/` and one test directory under
`tests/phase20_agents/`. **Do not edit any file outside your own** —
parallel worktrees merge cleanly only if there are no overlapping
changes.

---

## Shared contract

Import everything from one place:

```python
from src.research.agents.contract import (
    AgentResult, Claim, SourceTag, AGENT_SECTIONS,
)
```

### `Claim` — one tagged finding

Five `SourceTag` values; field requirements vary by tag.

| `source_tag` | Required | Optional | Use for |
|---|---|---|---|
| `PUBLIC`    | `text`, `source_url` | `date` | Quotes / facts pulled from a public URL |
| `INFERRED`  | `text`, `inference_logic` | `source_url`, `date` | Numbers derived from public data + industry benchmarks |
| `INTERNAL`  | `text`, `source` | `date` | HubSpot / Gong / internal records |
| `NOT_FOUND` | `text` | — | Explicit "searched and found nothing" — counts as good output |
| `ERROR`     | `text` | — | Agent failed; section unavailable |

`Claim` validates in `__post_init__` — if you build a `PUBLIC` claim
without `source_url` it raises `ValueError`. Tests must round-trip
`Claim.to_dict()` successfully.

### `AgentResult` — what `run()` returns

```python
@dataclass
class AgentResult:
    agent_name: str          # spec-stable, e.g. "agent_1_network_footprint"
    section_title: str       # for the Slack header
    claims: List[Claim]      # may include NOT_FOUND/ERROR claims mixed in
    duration_ms: Optional[int]
    notes: Optional[str]     # surfaced in research_gaps only
```

Use `AgentResult.error(agent_name, section_title, reason)` for total
failure — produces a single-ERROR-claim result.

---

## Agent module shape

```python
# src/research/agents/agent_N_<short_name>.py
"""<one-line purpose per spec §6 Agent N>."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.research.agents.contract import (
    AgentResult, Claim, SourceTag,
)


@dataclass
class AgentContext:
    """Minimal context every agent receives.

    Defined per-agent rather than shared so each agent can declare which
    inputs it actually needs — the dispatcher will only fill the ones
    declared. Keep this dataclass simple; if you find yourself adding
    runtime dependencies, take them as constructor args on a client class
    instead.
    """
    account_name: str
    account_domain: Optional[str] = None
    intent: Optional[str] = None
    # Add fields ONLY if your agent uses them.


async def run(ctx: AgentContext) -> AgentResult:
    """The entry point the dispatcher calls.

    Wrap the agent's external calls (Exa, EDGAR, HubSpot, etc.) so a
    timeout / 5xx / unhandled error returns an `AgentResult.error(...)`
    instead of raising. The dispatcher catches at the boundary too, but
    being defensive here keeps the section title visible to the rep
    when something goes wrong.
    """
    # ... per-agent implementation ...
    return AgentResult(...)
```

---

## Source attribution rules

These are spec §7 verbatim. Every claim **must** carry one tag:

- `PUBLIC` — sourced from a publicly available URL captured in `source_url`
- `INFERRED` — derived from public data + industry benchmarks; capture
  the math/reasoning in `inference_logic`
- `INTERNAL` — sourced from HubSpot, Salesforce (V2, not in scope), or
  Gong; capture the system + record id in `source`
- `NOT_FOUND` — the agent searched and could not source the data point;
  describe the gap in `text` so the rep can ask in discovery
- `ERROR` — the agent itself failed (timeout, 5xx, parse failure)

**No silent inference.** If you didn't find it in a source and don't
have a benchmark to infer from, emit `NOT_FOUND`. Do not guess.

**Never invent URLs.** A `source_url` must come from a real search /
EDGAR / Apollo / HubSpot response. The aggregator runs `assert_safe_url`
again before rendering, but you should not produce ill-formed URLs in
the first place.

---

## Testing requirements (every agent)

Place tests in `tests/phase20_agents/agent_N/test_*.py`. Each agent
ships pytest coverage for:

1. **3 representative accounts.** A public company (Walmart), a
   private mid-market 3PL (GEODIS), and an outside-ICP control (Notion
   or similar). Mock the external API client; assert the agent emits a
   well-formed `AgentResult` with at least one tagged claim per account.
2. **`NOT_FOUND` behavior.** When the upstream client returns no data,
   the agent emits a `NOT_FOUND` claim describing what was searched —
   never an empty `claims` list.
3. **Error handling.** When the upstream raises, the agent returns an
   `AgentResult` with a single ERROR claim and does not re-raise. Log
   line carries `type(e).__name__` only, not `str(e)` (CLAUDE.md log
   hygiene).
4. **Schema enforcement.** Every claim passes `Claim.__post_init__`
   without raising (asserted by constructing the result and calling
   `.to_dict()`).

Mock external clients at the import path inside the agent module — do
NOT make real Exa / EDGAR / Apollo calls in tests.

---

## What your subagent is responsible for

When you receive a worker prompt:

1. Read this contract doc and the relevant agent description in
   `docs/account-research-bot-spec.md` §6 and the May 26 spec §6.
2. Create exactly one new file: `src/research/agents/agent_N_<short_name>.py`.
3. Create tests under `tests/phase20_agents/agent_N/` — at minimum the
   four cases above.
4. **Do not touch** `findings_builder.py`, `output_formatter.py`,
   `runner.py`, the `contract.py` module, or anyone else's agent file.
   Phase 3 integration (task #6 in the orchestrator's task list) is
   the only step that touches the dispatcher.
5. Run `python3 -m pytest tests/phase20_agents/agent_N/` and report
   green tests plus the sample output for all 3 test accounts. Do not
   merge to `feat/v1-may26` yourself — the orchestrator will merge
   each worktree once it reviews the sample output.

---

## Existing utilities you can lean on

- **Exa client:** `from src.integrations.exa.client import ExaSearchClient`
  (mock its `.search()` method in tests).
- **EDGAR client:** `from src.integrations.edgar import EdgarClient`
  (mock the `.fetch_10k()` method).
- **URL safety:** `from src.security.url_guard import assert_safe_url`
  (run before storing any source_url; raises on private ranges).
- **Logger hygiene:** `from src.security.exception_logger import safe_log_exception`
  (use this in your `except` block; do not pass `str(e)`).

Existing extractor (`src/research/findings_builder.py`) is **read-only
reference**, not something you modify. It will be replaced by the
dispatcher in task #6 — your agents are the replacement.
