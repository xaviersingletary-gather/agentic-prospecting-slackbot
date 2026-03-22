# Observability — Gather AI Prospecting Bot

Feedback mechanisms must be configured before execution begins. Two layers: in-workflow signals agents detect and surface to the rep, and structured logging for debugging.

---

## Layer 1: In-Workflow Feedback Signals

No silent failures. Every agent detects and handles these conditions explicitly.

| Agent | Signal | Response |
|---|---|---|
| Input Normalizer | `confidence < 0.7` | Ask rep a single clarification question before proceeding |
| Input Normalizer | Account not found in HubSpot or Clay | Surface to rep: "I couldn't find [account] — can you confirm the company name?" |
| Persona Discovery | Clay returns 0 results | Surface to rep: "No personas found. Want me to try broader title keywords?" |
| Persona Discovery | Clay returns < 3 results | Surface to rep with count: "Only found N personas — continuing with limited results" |
| Persona Discovery | LinkedIn scrape fails | Continue without signals; note on persona card: "No LinkedIn signals available" |
| Scorer | No Google Drive account plan found | Continue; note on persona card: "No account plan found — scoring on persona type only" |
| Scorer | No Gong transcripts found | Continue; `gong_hook = null`; no hook injected into sequences |
| Scorer | No HubSpot history found | Continue; score on defaults only |
| Sequence Generator | Value driver cannot be determined | Default to `cycle_count_accuracy` for TDM/ODM; `network_roi` for FS |
| Any agent | External API returns 5xx | Retry once after 5 seconds; surface error to rep and halt that agent only |
| Any agent | External API rate limited | Exponential backoff; max 3 attempts before surfacing to rep |

---

## Layer 2: Structured Logging

### Agent Run Log Schema

Every agent execution writes one row to `agent_logs`:

```python
{
  "session_id": "uuid",
  "agent": "persona_discovery",
  "phase": 2,
  "account": "Nestlé",
  "rep_id": "U12345",
  "started_at": "2026-03-22T10:00:00Z",
  "completed_at": "2026-03-22T10:00:08Z",
  "duration_ms": 8234,
  "input": { ... },
  "output": { ... },
  "signals": [],
  "errors": [],
  "status": "success" | "partial" | "failed"
}
```

### Workflow Event Log Schema

Every rep interaction writes one row to `workflow_events`:

```python
{
  "session_id": "uuid",
  "event_type": "persona_approved",
  "phase": 2,
  "rep_id": "U12345",
  "payload": { ... },
  "timestamp": "2026-03-22T10:01:00Z"
}
```

**Event types:**
`session_started` | `intent_confirmed` | `intent_corrected` | `persona_approved` | `persona_rejected` | `step_edited` | `step_approved` | `sequence_approved` | `brief_delivered`

### Log Destinations

| Type | Destination |
|---|---|
| Agent run logs | PostgreSQL `agent_logs` table |
| Workflow events | PostgreSQL `workflow_events` table |
| App stdout/stderr | `/var/log/prospecting-bot/app.log` via systemd |
| Unhandled exceptions | Sentry (`SENTRY_DSN` in `.env`) |

### Sentry Setup

```python
import sentry_sdk

sentry_sdk.init(
    dsn=os.getenv("SENTRY_DSN"),
    environment=os.getenv("ENVIRONMENT"),
    traces_sample_rate=1.0
)
```

Initialize in `src/main.py` before anything else runs.

---

## Production Signals to Watch

| Signal | What It Means |
|---|---|
| High `duration_ms` on Persona Discovery | Clay API slow or rate limiting |
| Many `partial` statuses on Scorer | Google Drive or Gong access issues |
| Rep editing same step 3+ times | Sequence Generator prompt needs tuning |
| Sessions stalling at Checkpoint 1 | Persona quality issue — wrong titles being searched |
| Sessions stalling at Checkpoint 2 | Sequence copy quality issue — messaging needs work |
| High `intent_corrected` event rate | Input Normalizer confidence threshold needs adjustment |
