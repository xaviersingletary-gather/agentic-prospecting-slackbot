# Gather AI — Account Research Bot

A Slack-based research copilot for Gather AI sales reps. DM the bot an
account name; ~3 minutes later you get a structured intel brief from 8
specialized research agents, all sources tagged. Ask follow-up
questions in the same thread — answers are grounded in the saved
research, never fabricated.

V1 is **research only**. ROI doc generation, email/asset drafting, and
CRM write-back are V2+.

---

## Quick start

1. Open a DM with the bot (or post in any channel where it's invited).
2. Type an account name. Plain or conversational both work:

   ```
   Walmart
   research Sysco Foods
   look up GEODIS
   ```

3. The bot will ask one of two things:
   - **Disambiguation card** when the name is ambiguous (e.g. Volvo
     Group vs. Volvo Cars). Pick one.
   - **ICP override card** when the account looks outside Gather AI's
     ICP. Click "Research anyway" to proceed, or "Never mind" to drop.

4. Pick an **intent** from the four-button card:
   - **Prospecting** — first-touch outbound prep
   - **Meeting Prep** — research before a scheduled call
   - **Asset Building** — gathering ROI/case-study inputs
   - **General Research** — exploratory dig

5. Wait ~2–3 minutes. The bot posts the research brief as a threaded
   reply.

6. To ask a follow-up: `@`-mention the bot in that same thread.

   ```
   @bot what's their DC count again?
   @bot which persona should I lead with for the CFO angle?
   ```

   Answers come from the saved research only — no new web fetches per
   follow-up.

---

## What each agent does

Eight agents run in parallel against one target. Each emits a section
with source-tagged claims.

| # | Agent | Source signal |
|---|---|---|
| 1 | **Network Footprint** | DC count, square footage, location breakdown — Exa-sourced press / 10-K / corp site. Pallet positions are inferred when sqft is known (formula in the claim). |
| 2 | **Operating Baseline** | Revenue, headcount, growth signals. EDGAR 10-K for public companies; news + press for private. |
| 3 | **Board Priorities** | Investor day commentary, earnings call quotes, 10-K risk-section signals. |
| 4 | **Customer-Facing Signals** | Retailer chargebacks, NPS/Glassdoor/Reddit sentiment, customer service issues. |
| 5 | **Shrink & Compliance** | 10-K risk-section quotes on shrink/audit, FDA recalls, regulatory news. |
| 6 | **Automation Stack** | WMS / LMS / ERP / robotics signals. Job postings are the best signal — automation manager titles, vendor-specific JD keywords. |
| 7 | **Department Angles** | Per-persona pain (TDM / ODM / FS / IT / Safety). Synthesized from agents 1–6; no external calls. |
| 8 | **Contacts** | Apollo profiles tagged against HubSpot for prior touches + ownership. (Salesforce enrichment is V2.) |

Agents run concurrently with per-agent timeouts. A slow or failing
agent doesn't block the others — its section renders as an `[error]`
chip and the other seven still complete.

---

## Source attribution

Every claim in the research brief carries one of five tags:

| Tag | Meaning | Required field |
|---|---|---|
| `[public]` | Quoted / linked from a publicly available URL | source URL |
| `[inferred]` | Derived from public data + industry benchmark | inference logic |
| `[internal]` | Sourced from HubSpot / Gong | system + record id |
| `[not found]` | Searched and nothing surfaced — explicit gap | — |
| `[error]` | Agent failed; section unavailable | — |

**No silent inference.** If an agent didn't find data and has no
benchmark to infer from, it emits `[not found]` so you know to ask in
discovery.

**Never invent URLs.** Every `source` link came from a real upstream
response (Exa / EDGAR / Apollo / HubSpot) and was URL-safety-validated.

---

## When data is missing

You'll often see `[not found]` sections — that's working as intended.
The agent is telling you "I looked here, didn't find it."

What to do:
- **Take it to discovery.** A `[not found]` field is a discovery
  question — "I don't see a WMS of record in public sources; what are
  you running today?"
- **Re-run if the account is in flux.** A WMS migration announcement
  from this week won't be in last quarter's Exa cache. Send the
  account name again — the bot opens a fresh thread.
- **Ask in-thread.** If you remember a fact the agents missed
  ("aren't they on Blue Yonder?"), the follow-up answer will tell you
  "I don't have that in the research" rather than confirm — that's
  the hallucination guard working.

---

## Slash commands

| Command | Who | What |
|---|---|---|
| `/research-bot-stats` | Admin allowlist (set via `ADMIN_SLACK_USER_IDS`) | Posts a counts + averages summary of the last ~100 usage events (new_query, followup, agent_failure, disambig). |

The `/about` and other commands from earlier prototypes are not part
of V1.

---

## Logs

Two destinations:

- **Postgres on Railway** — `account_research` (keyed on Slack
  `thread_ts`), `conversation_turns`, `workflow_events`, plus legacy
  `sessions` / `personas` tables.
- **JSONL on Railway disk** — `./logs/usage.jsonl`, four typed event
  rows per spec §7 step 6.

`/research-bot-stats` reads the JSONL; the Railway dashboard surfaces
the same file as the runtime log volume.

---

## Architecture (one diagram)

```
Slack DM
  ↓
Disambig (LLM) → ICP gate (LLM) → Intent card
  ↓ (rep picks Prospecting / Meeting Prep / Asset Building / General Research)
8-agent dispatcher (asyncio.gather, per-agent timeout)
  ↓                                       ↓
agents 1–6, 8 (parallel)             agent 7 (synthesizes priors)
  ↓
Aggregator → research_blob (JSON)
  ↓
Upsert AccountResearch (keyed on thread_ts)
  ↓
Slack renderer (source-tag chips, 8 sections, timestamp)
  ↓
chat.postMessage (chunked if > 2800 chars)

Thread reply:
  thread_ts → AccountResearch lookup
  ↓
build_followup_context_v2(blob, last 20 conversation_turns, question)
  ↓
OpenRouter (no tools wired — read-only summarization)
  ↓
Reply → persist user + assistant turns to conversation_turns
```

---

## Sample outputs (anonymized)

### 1. Walmart — Prospecting intent

```
:office: Walmart
Research pulled May 26, 2026 at 2:14pm ET
Intent: Prospecting

Network Footprint
• [public] Operates 211 distribution / fulfillment facilities across
  North America — source — 2026-03-12
• [inferred] ~175,000,000 sq ft total warehouse footprint estimated
  (industry-standard ~750k sqft/grocery DC × 211 DCs)
  basis: revenue × industry-standard pallet throughput
• [inferred] ~11,666,666 pallet positions
  basis: sqft × 0.60 × 4 / 36

Operating Baseline
• [public] Latest 10-K on file (2026-01-31) — source — 2026-03-15
• [public] Headcount: 2,100,000 employees — source

Board Priorities
• [public] Q1 2026 earnings — CFO outlined "automation in DCs, cost
  reduction, and shrink containment" as top strategic priorities —
  source — 2026-05-15

Customer-Facing Signals
• [public] Suppliers protest Walmart OTIF chargebacks — source — 2025-08-04

Shrink & Compliance
• [public] 10-K Risk Factors: "inventory shrinkage" cited as
  material — source — 2026-01-31
• [public] $3B in inventory shrink write-offs reported — source — 2025-Q4

Automation Stack
• [public] Walmart expands Symbotic robotics rollout to 25 DCs —
  source — 2025-04-10

Department Angles / Personas
• [inferred] TDM — Owns the technical evaluation; lines up with the
  automation/CI charter. Anchor: "Symbotic robotics deployment to 25
  regional DCs, integrating with the WMS."
• [inferred] FS — Approves budget; frames the P&L impact. Anchor:
  "$3B in inventory shrink write-offs as a margin pressure."
• ... (5 personas total)

Contacts
• [internal] Jane Doe — VP Operations — owner: rep@gather.ai — prior
  touch (HubSpot contact 12345)
• [public] John Cold — Director CI — source
```

### 2. GEODIS — Meeting Prep intent

```
:office: GEODIS
Research pulled May 26, 2026 at 9:01am ET
Intent: Meeting Prep

Network Footprint
• [public] 300 distribution / fulfillment facilities globally — source
• [inferred] ~120,000,000 sq ft estimated total footprint
  basis: 300 DCs × 400k sqft (3PL benchmark)

Operating Baseline
• [public] Revenue $11.2B (per press) — source — 2025-04
• [public] Headcount 53,000 — source

Board Priorities
• [public] GEODIS board endorses automation investment for 2025-26 —
  source — 2025-02-10

Customer-Facing Signals
• [public] r/logistics thread on GEODIS shipping accuracy — source

Shrink & Compliance
• [public] Supply chain regulatory update — source — 2025-06-04

Automation Stack
• [public] Automation Lead job posting — Lead automation initiatives
  across GEODIS facilities — source

Department Angles / Personas
• ... (5 personas, mix of [inferred] and [not found])

Contacts
• [not_found] No Apollo or HubSpot contacts surfaced for this account.
  (Apollo tagging skipped — set HUBSPOT_ACCESS_TOKEN to enable);
  Salesforce ownership enrichment deferred to V2.
```

### 3. Notion — General Research intent (outside-ICP)

```
:warning: Notion looks outside Gather AI's ICP — pure SaaS, no
warehouse operations.
Want me to research it anyway?
[Research anyway] [Never mind]
```

→ rep clicks "Research anyway"

```
:office: Notion
Research pulled May 26, 2026 at 4:33pm ET
Intent: General Research

Network Footprint
• [not_found] No public DC count located for Notion.
• [not_found] No public square-footage figure sourced and industry
  not specified — falling back to no estimate.

Operating Baseline
• [not_found] No public revenue figure found for Notion (searched
  SEC filings + news/press).
• [not_found] No public headcount figure found for Notion.

... (all 8 sections in the same shape — explicit not-found, no
silent inference)
```

The outside-ICP confirm + the explicit `[not_found]` everywhere are
the bot doing its job: telling you the account isn't a fit and the
research won't compensate.

---

## For operators

### Deploying

Push to `main` → Railway picks it up automatically.

Required env vars (set in the Railway dashboard, not in `.env`):

- `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `SLACK_SIGNING_SECRET`
- `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`, `OPENROUTER_BASE_URL`
- `EXA_API_KEY`
- `APOLLO_API_KEY`
- `HUBSPOT_ACCESS_TOKEN`, `HUBSPOT_PORTAL_ID`
- `DATABASE_URL` (Postgres)
- `ADMIN_SLACK_USER_IDS` (comma-separated, for `/research-bot-stats`)

Missing keys degrade gracefully — the relevant agent emits
`[not found]` rather than crashing.

### Tests

```bash
python3 -m pytest tests/ -q
```

Phases 1–23 plus the agent and assembly suites. The two legacy
`phase16/test_e2e_v1.py` cases are skipped (superseded by the
phase20–22 suites against the new pipeline).

### Source layout

```
src/
├── handlers/           # Slack interaction handlers
│   ├── dm_research.py        # New-query entry point
│   ├── intent_capture.py     # Disambig + intent buttons
│   ├── icp_gate.py           # ICP sanity check
│   ├── followup_qa.py        # Thread-based Q&A
│   └── research_bot_stats.py # /research-bot-stats
├── research/
│   ├── agents/         # The 8 research agents + dispatcher + runner
│   │   ├── contract.py       # AgentResult / Claim / SourceTag
│   │   ├── dispatcher.py     # asyncio.gather across all 8
│   │   ├── aggregator.py     # results → research_blob
│   │   ├── renderer.py       # research_blob → Slack blocks
│   │   ├── runner_v1.py      # dispatch → persist → render → post
│   │   └── agent_{1..8}_*.py # one module per agent
│   └── account_research_store.py  # AccountResearch + ConversationTurn CRUD
├── agents/             # Legacy prospecting-bot agents (Phase 5+ deprecated)
├── integrations/       # Exa / EDGAR / Apollo / HubSpot clients
├── db/                 # SQLAlchemy models + session
├── security/           # safe_mrkdwn, URL guard, admin allowlist, log redact
└── usage/              # JSONL event logging
```
