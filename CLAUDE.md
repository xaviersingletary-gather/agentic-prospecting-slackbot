# CLAUDE.md — Gather AI Prospecting Bot

## What This Is

A multi-agent Slack bot that takes a free-form rep message about a target account and orchestrates persona discovery, enrichment, scoring, value mapping, and sequence generation. Output is a ready-to-paste sequence brief delivered in Slack. Nothing reaches a prospect without rep approval.

Full context:
- Spec: `docs/spec.md`
- TDD Execution Plan: `docs/tdd-plan.md`

---

## Tech Stack

- **Language:** Python 3.11+
- **Agent framework:** Claude Agent SDK (Anthropic)
- **LLM:** OpenRouter (`OPENROUTER_MODEL` in `.env`) — do not hardcode any model
- **Slack:** Slack Bolt for Python, Socket Mode
- **Database:** PostgreSQL (session state, edit history, workflow phase)
- **Hosting:** Railway (one account, one service per app)
- **Deploy:** Push to GitHub → Railway auto-deploys

---

## Project Structure

```
/
├── CLAUDE.md
├── README.md
├── .env.example
├── .gitignore
├── docs/
│   ├── spec.md
│   └── tdd-plan.md
├── src/
│   ├── main.py              # Slack Bolt app entry point
│   ├── agents/
│   │   ├── normalizer.py    # Agent 1: Input Normalizer
│   │   ├── discovery.py     # Agent 2: Persona Discovery
│   │   ├── scorer.py        # Agent 3: Scorer & Value Mapper
│   │   ├── generator.py     # Agent 4: Sequence Generator
│   │   ├── editor.py        # Agent 5: Sequence Editor
│   │   └── delivery.py      # Agent 6: Sequence Brief Delivery
│   ├── integrations/
│   │   ├── hubspot.py
│   │   ├── clay.py
│   │   ├── gong.py
│   │   ├── google_drive.py
│   │   └── slack_blocks.py  # Slack UI block builders
│   ├── db/
│   │   ├── models.py        # SQLAlchemy models
│   │   └── session.py       # DB connection
│   └── config.py            # Loads .env, exposes settings
├── tests/
│   ├── phase1/
│   ├── phase2/
│   ├── phase3/
│   ├── phase4/
│   ├── phase5/
│   └── phase6/
└── requirements.txt
```

---

## Running the App

```bash
# Install dependencies
pip install -r requirements.txt

# Run locally
python src/main.py
```

The app connects to Slack via Socket Mode — no inbound webhook or nginx needed.

## Deployment

This app runs on Railway. There is no server to SSH into.

- Environment variables are set in the Railway dashboard (not in a `.env` file on the server)
- PostgreSQL is a Railway add-on — `DATABASE_URL` is injected automatically
- Deploy by pushing to the `main` branch on GitHub — Railway picks it up automatically
- Logs are visible in the Railway dashboard

Railway requires a `Procfile` or `railway.toml` in the repo root to know how to start the app:

```
# Procfile
worker: python src/main.py
```

Use `worker` not `web` — this app has no HTTP server, it connects to Slack via WebSocket.

---

## Explicit Constraints

- **Never commit `.env`** — real credentials live on the server only
- **Never hardcode model names** — always read from `OPENROUTER_MODEL` env var
- **Never generate or send sequences without approved personas in session state** — check DB before every sequence generation call
- **Never skip a checkpoint** — Checkpoint 1 (persona approval) and Checkpoint 2 (sequence approval) must both be enforced; no workflow phase advances without explicit rep confirmation
- **Never push to GitHub with secrets** — `.gitignore` covers `.env` and `*.json` credential files
- **All external API calls must handle errors gracefully** — if Clay, Gong, or Drive return nothing, agents continue with null and surface the gap to the rep rather than failing
- **One account per session** — do not allow a new account workflow to start until the current session is resolved or explicitly cancelled
- **Tests must pass before moving to the next phase** — follow `docs/tdd-plan.md` strictly; do not start Phase N+1 until all Phase N tests pass

---

## Ralph Wiggum Plugin

This project uses the [ralph-wiggum plugin](https://github.com/anthropics/claude-code/tree/main/plugins/ralph-wiggum) for iterative, test-driven phase execution.

### How to use it for each TDD phase

Each phase in `docs/tdd-plan.md` maps to a ralph loop. Run one phase at a time:

```bash
/ralph-loop "Build Phase 1 of docs/tdd-plan.md.
- Set up Slack Bolt app in src/main.py using Socket Mode
- Build the Input Normalizer agent in src/agents/normalizer.py
- All tests in tests/phase1/ must pass
- Confirmation card must render correctly in Slack
- Output: <promise>PHASE_1_COMPLETE</promise>" \
--max-iterations 20 \
--completion-promise "PHASE_1_COMPLETE"
```

Replace `Phase 1` / `PHASE_1_COMPLETE` with the current phase number. Always set `--max-iterations` to prevent runaway loops.

### Completion promise pattern

Each phase's completion promise must match what's in the prompt exactly:

| Phase | Completion Promise |
|---|---|
| 1 | `PHASE_1_COMPLETE` |
| 2 | `PHASE_2_COMPLETE` |
| 3 | `PHASE_3_COMPLETE` |
| 4 | `PHASE_4_COMPLETE` |
| 5 | `PHASE_5_COMPLETE` |
| 6 | `PHASE_6_COMPLETE` |

### Do not start the next phase until

1. All tests in `tests/phaseN/` pass
2. The demo defined in `docs/tdd-plan.md` for that phase has been verified by a human
3. `/cancel-ralph` has been run to cleanly exit the loop

---

## Observability

Sentry is initialized in `src/main.py` before anything else runs. Never remove it.

Every agent must log to the `agent_logs` table on start and completion — including failures. Every rep interaction must log to `workflow_events`. No silent failures anywhere in the codebase — if an integration returns nothing, log it and surface it to the rep.

See `docs/observability.md` for the full signal map and log schema.

---

## Session State (PostgreSQL)

Session state persists across agent calls. Key tables:

- `sessions` — one row per active workflow (account, rep, phase, status)
- `personas` — discovered and scored personas per session
- `sequences` — generated sequences and steps per persona
- `edit_history` — edit events per sequence step

Do not store state in memory — always read from and write to the DB so the workflow survives restarts.
