# Gather AI — Agentic Prospecting Slack Bot

A multi-agent workflow that helps Gather AI sales reps generate personalized, research-backed outreach sequences for target accounts — directly from Slack.

## How It Works

A rep sends a free-form message in Slack naming a target account. The bot orchestrates a pipeline of specialized agents that discover personas, enrich and score them, map value drivers, and generate ready-to-use sequences — with human-in-the-loop approval at every step.

```
Rep Slack message → Input Normalizer → Persona Discovery → Scorer & Value Mapper
→ [Checkpoint 1: Rep approves personas]
→ Sequence Generator (parallel per persona)
→ [Checkpoint 2: Rep edits & approves sequences]
→ Sequence Brief delivered (copy-paste into Apollo)
→ Reply data → HubSpot
```

## Docs

- [`docs/spec.md`](docs/spec.md) — Full Specification Artifact (data models, agent architecture, Slack UX, integration contracts)
- [`docs/tdd-plan.md`](docs/tdd-plan.md) — TDD Execution Plan (6 phases, test criteria, demo definitions)

## Status

Currently in planning phase. Spec and TDD plan complete. Next: Step 4 — Provisioning (MCP connections + tool setup).

## Open Items

- Sequence templates (AE lane vs MDR lane — step count, channel mix, day offsets)
- Apollo upgrade path for v2
