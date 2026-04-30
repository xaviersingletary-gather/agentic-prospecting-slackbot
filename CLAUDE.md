# CLAUDE.md — Gather AI Account Research Bot

## What This Is

A Slack bot that researches target accounts on demand. AE or MDR types `/research [Account Name]`, selects target personas, and receives structured account intelligence: trigger events, competitor signals, DC/facility intel, board initiatives, and research gaps. All output is sourced. No messaging is generated until V2.

Full context:
- Primary spec: `docs/account-research-bot-spec.md`
- Legacy spec (Python prototype): `docs/spec.md`
- TDD Execution Plan: `docs/tdd-plan.md`

---

## Tech Stack

- **Language:** Python 3.11+
- **Agent framework:** Claude Agents SDK (Anthropic)
- **Slack:** Slack Bolt for Python, Socket Mode
- **APIs:** Apollo (contact sourcing), Exa (web research), HubSpot (CRM), Salesforce (CRM)
- **State:** Railway-persisted JSONL files for V1 (no database in V1)
- **Test runner:** pytest + pytest-asyncio + pytest-mock
- **Hosting:** Railway (one account, one service per app)
- **Deploy:** Push to GitHub → Railway auto-deploys

---

## Project Structure

```
/
├── CLAUDE.md
├── README.md
├── requirements.txt
├── .env.example
├── .gitignore
├── docs/
│   ├── account-research-bot-spec.md   # Primary spec
│   ├── spec.md                         # Legacy Python prototype spec
│   └── tdd-plan.md
├── src/
│   └── (Python source)
├── tests/
│   └── (pytest tests — one directory per phase)
└── logs/
    ├── usage.jsonl
    └── lick_outcomes.jsonl
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
- Deploy by pushing to the `main` branch on GitHub — Railway picks it up automatically
- Logs are visible in the Railway dashboard

Railway requires a `Procfile` or `railway.toml` in the repo root to know how to start the app:

```
# Procfile
worker: python -u src/main.py
```

Use `worker` not `web` — this app has no HTTP server, it connects to Slack via WebSocket.

---

## Explicit Constraints

- **Never commit `.env`** — real credentials live on Railway env vars only
- **Never hardcode model names** — read from env var
- **Never send contacts to Apollo sequences without explicit AE confirmation click** — no auto-enrollment ever
- **Never omit a research section** — if no data, show "No public data found"; never silently skip a section
- **Never include a DC count claim without a source URL** — block it from output entirely
- **Never push to GitHub with secrets** — `.gitignore` covers `.env` and credential files
- **All external API calls must handle errors gracefully** — if Exa, Apollo, HubSpot, or Salesforce return nothing, surface the gap explicitly rather than failing
- **Tests must pass before moving to the next phase** — follow `docs/account-research-bot-spec.md` phase order; do not start Phase N+1 until Phase N acceptance criteria pass
- **Log every API call with latency** — format: `[API] exa.search — 1240ms`
- **Acknowledge Slack interactions within 3 seconds** — use `ack()` immediately, defer all heavy processing async

---

## Security Considerations

Carried forward from the audit of the Python prototype (2026-04-30). Encode these as test cases in the relevant phase, not as afterthoughts.

### Authorization
- **Every state-mutating Slack handler must verify `body.user.id === session.rep_id` before mutating.** Workspace membership is not authorization — anyone can click a button on someone else's thread. Centralize the check in a Bolt middleware so no handler can skip it.
- **Slash commands are not gated by default.** `/research`, `/admin/usage`, and any future admin command must check the caller against an allowlist (Slack user ID or workspace admin role).

### Slack output safety
- **Treat all external strings as untrusted mrkdwn.** Apollo, Clay, HubSpot, Salesforce, and Exa results may contain attacker-controlled `<http://evil|click here>` payloads that phish other workspace members. Run every interpolated field through a `safeMrkdwn()` helper that strips `<`, `>`, `|`, and `&` before it lands in a Block Kit text block.
- **Never render LLM-generated text directly into Slack without escaping.** Prompt-injected content can produce mrkdwn links the same way poisoned API data can.

### Input → log hygiene
- **Do not log raw user text from slash commands.** Reps paste tokens by accident. Log lengths, hashes, or the parsed account name only.
- **Catch exceptions narrowly and log `type(e).__name__`, not `str(e)`.** Stringified exceptions from Google/Apollo/HubSpot SDKs can contain partial key material or full request bodies.
- **Sentry breadcrumbs must not include request/response bodies for any external API.** Whitelist the fields that get attached.

### SSRF
- **Any URL fetched server-side (Exa results, EDGAR filings, document_fetcher equivalents) must pass through an allowlist or block-list of hostnames.** Railway runs in a shared network; private IP ranges (`10.0.0.0/8`, `169.254.0.0/16`, `127.0.0.0/8`) must be rejected before the HTTP call.
- **LLM-rewritten URLs are user input.** If the agent constructs or modifies a URL, validate it the same way you would a query-string param.

### LLM prompt injection blast radius
- **External content (Exa pages, Apollo bios, HubSpot notes) must never flow into a prompt that can call a tool with side effects** (HubSpot writes, Salesforce writes, Apollo enrollment, Slack posts to other channels). The trust boundary: poisoned content → read-only summarization is fine; poisoned content → write action is not.
- **Apollo enrollment stays manual.** No code path enrolls a contact in a sequence without an explicit AE button click. This is also called out in the constraints above; the security risk is that an LLM tool-call loop could be tricked into enrolling competitor contacts on a poisoned page.

### Database & secrets
- **Use a `with`/`async` session helper for every DB interaction.** Hand-rolled `SessionLocal()` + `try/finally` leaks connections on early return; the prototype hit pool exhaustion this way.
- **Pin dependencies with exact versions (`==` / `^` lockfile), not `>=`.** Add Dependabot or Renovate so upgrades are reviewed, not silently picked up at deploy time.
- **Never read `.env` in production.** Railway env vars are the only source of truth. Local `.env` is for dev and is gitignored — verify before every commit.
- **Google service account JSON, if used, must be loaded from an env var or Railway secret file mount, not a path that could be logged.** Scopes must be minimal (`drive.readonly` not `drive`).

### Threat model summary
The realistic attackers are: (1) a curious workspace member escalating into another rep's session, (2) an external party who poisons a public web page that Exa indexes, hoping the agent will act on it. Both are addressed by the rules above. We are not defending against a compromised Slack workspace admin or a Railway insider.

---

## Phase Execution

Use the orchestration prompt in `docs/account-research-bot-spec.md` (Part 2) to drive phased builds via Claude Code sub-agents. One phase at a time — never start Phase N+1 until Phase N acceptance criteria all pass.

| Phase | Spec Section | Description |
|-------|-------------|-------------|
| 1 | §1.1 | Railway deployment + health check |
| 2 | §1.2 | Research output format, no messaging |
| 3 | §1.3 | Persona lock (4 checkboxes) |
| 4 | §1.4 | Source citation enforcement |
| 5 | §1.5 | Usage tracking (JSONL + /admin/usage) |
| 6 | §1.6 | /about command |
| 7+ | §1.2.1+ | HubSpot, Salesforce, LICK — require sign-off |

---

## State & Persistence (V1)

No database in V1. State uses:
- **In-memory:** persona selection keyed by Slack user ID + timestamp (survives the interaction callback; lost on restart — acceptable for V1)
- **Railway-persisted files:** `./logs/usage.jsonl`, `./logs/lick_outcomes.jsonl`

Upgrade to Redis in V2 when session persistence across restarts is required.
