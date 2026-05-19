# Spec: V1 Daily-Use Research Copilot

**Date:** 2026-05-19
**Owner:** Xavier Singletary / Gather AI
**Supersedes:** `docs/followup-qa-spec.md` (now folded into Move 2 below)
**Status:** Draft — pending sign-off

---

## 1. Purpose

Today the bot's mental model is **research request → output → done**. That gets used once per account and then forgotten. For daily adoption by AEs and MDRs, we need to flip the shape:

> **A Slack thread is a living workspace for an account. The bot is the copilot that lives inside it. The research brief is just the first thing it does.**

Once that shape is in place, three of Peter's use cases collapse into one product:

1. Outbound prospecting — "I'm starting fresh on Volvo Group, prep me."
2. Pre-call glance — "What's new on Walmart since I last looked?"
3. Ad-hoc deep-dive — "@bot which contact should I lead with for the CFO angle?"

All three live in the same thread, against the same persisted artifacts. That's V1.

V2 (asset creation: ROI doc, "cost of inaction" doc, email drafts) and V3 (distribution, attribution, CRM write-back) are out of scope here and will be specced separately.

---

## 2. Scope

### In — four moves
1. **Intent capture before research** — a one-step disambiguation + intent card after the rep types an account name.
2. **Thread-based Q&A** — `@`-mention the bot in any research thread to ask grounded questions against saved artifacts.
3. **Smart suggested follow-ups** — 3–4 next-question buttons appended to the research brief, generated from the findings (not boilerplate).
4. **Diff-first cold-start** — if the bot has researched this account before, lead with the diff and let the rep choose what to do next.

### Out (V2/V3 — do not build in this batch)
- ROI doc / "cost of inaction" doc generation.
- Email / asset drafting.
- Apollo sequence enrollment (still requires explicit click; not in scope here).
- CRM write-back of research artifacts to Salesforce or HubSpot.
- UTM / attribution / meeting-rate tracking.
- Fresh Exa retrieval triggered by a follow-up question (Q&A is reason-only over existing artifacts).
- Cross-account memory (e.g., "what did I ask about Sysco last week?").
- Streaming token output.

---

## 3. Canonical User Flow

A single walkthrough that exercises all four moves. Every section below traces back to this flow.

```
Day 1 — Monday 9:02am
  Peter (DM): "Volvo"
  Bot:        [Intent card]
              "Quick check before I research:
               Which Volvo? • Volvo Group (trucks, buses, engines, marine)
                             • Volvo Cars (owned by Geely)
               Why are you looking? • Outbound first-touch
                                    • Pre-call prep
                                    • Renewal
                                    • Just digging"
  Peter: clicks "Volvo Group" + "Outbound first-touch"
  Bot:   "🔍 Researching Volvo Group for outbound first-touch…"
         [live progress card]
         → research brief card
         → contact list card
         → [Suggested next questions]
            • "What angle should I lead with for the CSCO?"
            • "Walk me through the Memphis DC opening"
            • "Compare automation stack vs. industry baseline"

Day 4 — Friday 8:45am
  Peter (DM, same channel): "Volvo Group"
  Bot:   "I last researched Volvo Group 4 days ago.
          🆕 New since Monday:
            • New press release: Q1 earnings call — CSCO referenced inventory
              accuracy as a 2026 priority
            • LinkedIn: Sarah Chen (TDM) posted about a Memphis DC ramp
          What do you want to do?
            [Re-research]  [Dig into what's new]  [Ask a specific question]"
  Peter: clicks "Ask a specific question" → types in thread:
         "@bot which contact should I hit first now that Q1 earnings dropped?"
  Bot:   [thinking placeholder, edited in <15s with the answer]
         "Sarah Chen (Director of CI) remains top priority — her Memphis post
          maps directly to the CSCO's earnings-call language about
          inventory accuracy. Mark Rivera is still secondary; he just hit
          5 months in role and hasn't surfaced any priority signals.
          _Answered from saved research for Volvo Group. No new sources fetched._"
```

That flow is the V1 success state. Every test in the TDD plan maps back to one of these moments.

---

## 4. Inputs / Outputs / Components

### Inputs
| Source | Used by |
|---|---|
| Slack `message` event (text, user, channel, ts, thread_ts) | Routing in `dm_research` |
| Slack action payloads (intent card, suggested-question buttons, diff-front-door buttons) | Action handlers |
| `Session` row (matched by `thread_ts == message.thread_ts` AND `rep_id == message.user`) | Q&A authz + diff-front-door |
| `CompanyResearch` row (latest by `created_at` for `session_id`) | Q&A context, suggested-questions LLM input |
| `Persona` rows (all for `session_id`) | Q&A context |
| `ContactResearch` rows (all for `session_id`) | Q&A context |
| `memory/snapshots` JSONL | Diff-first cold-start |
| Thread history via `conversations.replies` (last 20 messages) | Q&A multi-turn coherence |

### Outputs
| Destination | What gets written |
|---|---|
| Slack thread | Intent card, progress card, brief, contact list, suggested-question chips, diff-front-door card, Q&A answers |
| Postgres `Session.normalized_request` (JSON) | Captured intent (`disambiguation`, `intent_type`) |
| Postgres `WorkflowEvent` | One row per significant moment: `intent_captured`, `diff_front_door_shown`, `suggested_question_clicked`, `followup_question`, `forbidden_followup` |
| `logs/account_snapshots/{account_key}.jsonl` | Already written by existing `save_snapshot` — no change |

### System Components
| Component | Status | Role |
|---|---|---|
| `src/handlers/dm_research.py` | EXTEND | Branch on (thread_ts match? prior snapshot? top-level DM?) |
| `src/handlers/intent_capture.py` | NEW | Post intent card + handle intent-card button clicks |
| `src/handlers/followup_qa.py` | NEW | Q&A handler (Move 2) |
| `src/handlers/diff_front_door.py` | NEW | Re-entry card + button handlers (Move 4) |
| `src/agents/followup_agent.py` | NEW | Build prompt + call OpenRouter for Q&A |
| `src/agents/suggested_questions.py` | NEW | Generate 3–4 grounded follow-up questions from findings |
| `src/research/sessions.py` | EXTEND | `get_session_by_thread_ts(thread_ts, rep_id)` |
| `src/research/runner.py` | EXTEND | Accept captured intent; append suggested-questions block to brief |
| `src/integrations/slack_blocks.py` | EXTEND | New cards: `intent_capture_card`, `diff_front_door_card`, `suggested_questions_block` |
| `src/memory/snapshots.py` | NO CHANGE | Used as-is |
| `src/memory/diff.py` | NO CHANGE | Used as-is |
| `src/security/safe_mrkdwn.py` | NO CHANGE | Used to escape LLM output (Q&A, suggested questions) |

### Credentials Required
- [x] `OPENROUTER_API_KEY` — already configured.
- [x] `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `DATABASE_URL` — already configured.
- No new credentials.

---

## 5. Per-Move Logic

### Move 1 — Intent capture before research

**Trigger:** Top-level DM with an account name AND no recent snapshot for that account (if a snapshot exists, Move 4 wins — see below).

**Behavior:**
1. Parse account name as today (`_extract_account_name`).
2. Post an **intent capture card** with two question groups:
   - **Disambiguation** — only shown if the LLM judges the account name ambiguous (see below). Options sourced from a one-shot LLM call: *"Is the company name 'X' ambiguous? If yes, list the 2–4 most likely real-world entities with one-sentence distinguishers. If unambiguous, return null."* Cached on `Session.normalized_request.disambiguation_options`.
   - **Intent type** — always shown. Four fixed buttons: `Outbound first-touch`, `Pre-call prep`, `Renewal`, `Just digging`.
3. On submit, both choices are persisted to `Session.normalized_request`, a `WorkflowEvent(event_type="intent_captured")` is logged, and `run_account_research(session, ...)` fires with the intent passed through.
4. The research agents already vary their emphasis by `persona_filter` and `use_case_angle` (existing fields in `normalized_request`). Wire `intent_type` into the same agent prompts so the brief leans appropriately (e.g., outbound emphasizes trigger events + cold-open hooks; pre-call emphasizes recent news + the rep's flagged contacts; renewal emphasizes expansion signals + risk).

**Bypass:** If the rep DMs a string that exactly matches a known disambiguation (e.g., `"Volvo Group"` rather than `"Volvo"`), skip the disambiguation block but still show intent.

**Cost guard:** The ambiguity-check LLM call is cheap (Haiku, ~300 tokens). Cached per account name in a small in-memory dict + the Session row so a re-DM of the same name doesn't re-call.

---

### Move 2 — Thread-based Q&A (folds in the prior `followup-qa-spec.md`)

**Trigger:** Slack `message` event where `message.thread_ts` matches `Session.thread_ts`, `message.user == Session.rep_id`, and the bot is `@`-mentioned in `message.text`.

**Behavior:**
1. Within 3s, post a "_thinking…_" placeholder threaded off the original DM.
2. Pull session + latest `CompanyResearch` + all `Persona` rows + all `ContactResearch` rows + last 20 thread messages via `conversations.replies`.
3. Assemble context (system prompt grounds answers in provided artifacts only; no tool calls; no invented facts; ≤200 words unless asked).
4. Call OpenRouter (`OPENROUTER_MODEL`, `max_tokens=800`, `temperature=0.3`, `timeout=30`).
5. Pass answer through `safe_mrkdwn`.
6. `chat.update` the placeholder; fall back to `chat.postMessage` if update fails.
7. Append footer: `_Answered from saved research for {account_name}. No new sources fetched._`
8. Log `WorkflowEvent(event_type="followup_question", payload={question_length, answer_length, latency_ms})`. Question text NOT logged.

**Authz:** If `message.user != Session.rep_id`, post nothing. Log `forbidden_followup`.

**Empty mention:** If message text is just `<@BOT>`, reply with "Ask me a question about {account_name} and I'll answer from saved research."

**Cancelled session:** If `Session.status == "cancelled"`, reply with cancellation message; no LLM call.

**Research-in-flight:** If session exists but no `CompanyResearch` row yet, reply "I'm still finishing the initial research. Ask again in a minute."

**Rate limits:**
- 20 follow-ups / session / 24h.
- 100 follow-ups / rep / 24h.
- Enforced via SQL count on `WorkflowEvent`.

**Token budget:** Cap `raw_research_text` at 12k chars, total context at 28k chars.

---

### Move 3 — Smart suggested follow-ups

**Trigger:** After `run_account_research` produces a brief; before the brief is posted to Slack.

**Behavior:**
1. Call OpenRouter with the findings as input. Prompt: *"Given this account research, generate exactly 3–4 follow-up questions a sales rep would ask next to act on it. Each question must be answerable from the research above. Reference specific findings (a named DC, a named contact, a specific trigger event) — do not produce generic questions. Return as a JSON array of strings."*
2. Validate the JSON. If parse fails, fallback = 3 generic questions: *"Which contact should I hit first?"*, *"What's the strongest trigger event to lead with?"*, *"What are the biggest research gaps?"* Log the failure (don't crash).
3. Each question becomes a Slack button in an `actions` block appended to the brief card. Button text = the question; button `action_id` = `suggested_question`; button `value` = the question text.
4. Clicking a button posts the question into the thread *as if the rep typed it with `@`-mention* — reuses Move 2's pipeline. Logs `WorkflowEvent(event_type="suggested_question_clicked", payload={question_text})`.

**Cost guard:** One LLM call per research run. Synchronous with the brief render; if it takes >5s, ship the brief without buttons and log a `suggested_questions_slow` event.

---

### Move 4 — Diff-first cold-start

**Trigger:** Top-level DM with an account name AND `get_latest_snapshot(account_name)` returns a snapshot whose `saved_at` is < 14 days old.

**Behavior:**
1. Skip the intent card.
2. Compute `diff_findings(prev_snapshot, prev_snapshot)` — no, we don't have new findings yet. The diff render at this point shows the *prior* findings as a summary plus the saved_at timestamp.
3. Post a **diff front-door card** with three buttons:
   - `Re-research` — fires the full pipeline (with intent card first; this becomes the standard flow).
   - `Dig into what's new` — opens the existing brief in the same thread for Q&A. No new research run.
   - `Ask a specific question` — posts a hint message ("`@`-mention me with your question") and gives the rep a place to start.
4. If the snapshot is ≥14 days old, treat it as cold and run Move 1 (intent) → full research. The 14-day cutoff is configurable via env var `SNAPSHOT_FRESHNESS_DAYS`.

**Thread continuity:** The diff front-door card creates a new Session row with `thread_ts` set immediately (see Move 5 below — the Phase 1 fix). The original session's artifacts remain queryable in the prior thread; the new session inherits a *link* to the prior `CompanyResearch.id` so Q&A in the new thread can reach back. (Stored as `Session.normalized_request.prior_company_research_id`.)

---

### Move 5 — Foundation fix (not a user-facing move, but required)

`src/handlers/dm_research.py` does NOT currently persist `Session.thread_ts`. Move 2 cannot match a thread reply to a session without it. Fix in Phase 1:

```python
# in handle_research_dm, immediately after create_session(...)
session.thread_ts = message.get("ts")
session.channel_id = message.get("channel")
```

---

## 6. Edge Cases & Guardrails

| Case | Behavior |
|---|---|
| Rep types only `@bot` with no question | Hint message; no LLM call |
| Rep replies in a research thread without `@`-mention | Ignored |
| Rep replies in a research thread that belongs to a different rep | Ignored silently; `forbidden_followup` logged |
| Intent card is ignored by the rep for >30 min | Card stays valid; clicking it later still works |
| LLM returns an answer containing mrkdwn link payload `<http://evil|click>` | `safe_mrkdwn` strips before posting |
| Suggested-questions LLM call fails | Brief ships without buttons; fallback questions optional (see Move 3) |
| Snapshot is 13 days old, rep types account name | Diff front-door (Move 4) |
| Snapshot is 15 days old | Treated as cold; Move 1 (intent) → research |
| Disambiguation LLM call fails | Skip disambiguation block; show intent buttons only |
| Rep types an account name that's already a known disambiguation key (e.g., `"Volvo Group"`) | Skip disambiguation block; show intent buttons only |
| Rep clicks a suggested-question button after the rate limit is hit | Treat same as a Q&A — rate-limit message posted |
| `CompanyResearch` row missing when Q&A fires | "I'm still finishing the initial research" reply; no LLM call |
| Session is cancelled | Cancellation message; no LLM call |
| `chat_update` fails on Q&A answer | Fall back to `chat_postMessage` in the thread |
| Multiple concurrent follow-ups in same thread | Each gets its own placeholder; first to finish edits its own message |
| Account name extraction returns empty | Existing hint message ("Send me an account name…") |

---

## 7. Success Criteria

Each is specific, falsifiable, and traceable to one move.

### Move 1 — Intent capture
1. Given a DM with `"Volvo"` and no recent snapshot, the bot replies with an intent-capture card that includes BOTH a disambiguation block (≥2 options) AND four intent buttons.
2. Given a DM with `"Sysco"` (unambiguous) and no recent snapshot, the bot replies with an intent-capture card that includes ONLY the four intent buttons.
3. Clicking a disambiguation + intent combination persists both to `Session.normalized_request` and logs an `intent_captured` event.
4. The captured intent is passed to `run_account_research` and visible in the prompt sent to the research agents (verified via patched LLM call in tests).

### Move 2 — Thread Q&A
5. Given a thread with a completed session and a rep reply `@bot which contact first?`, the bot replies in the same thread with a Block Kit message referencing at least one persona from the session's `Persona` rows.
6. A reply from a different Slack user mentioning the bot in that thread produces no Slack post and logs a `forbidden_followup` event.
7. A thread reply with no `@`-mention produces no Slack post.
8. A question the artifacts cannot answer yields an explicit "I can't answer that from the saved research" reply — no fabricated facts (verified by injecting a question whose answer isn't in the test fixtures).
9. Slack ack within 3s (placeholder posted); p95 answer latency ≤30s (logged in `WorkflowEvent.payload.latency_ms`).
10. LLM output passes through `safe_mrkdwn` (verified by injecting `<http://evil|click>` into a mocked LLM response and asserting it's stripped).
11. After 20 follow-ups in 24h for a single session, the 21st returns the rate-limit message and does NOT call OpenRouter.
12. No Exa, Apollo, or HubSpot client is invoked from the Q&A code path (verified by patching those clients to raise on any call).

### Move 3 — Suggested follow-ups
13. The brief card includes an `actions` block with 3–4 buttons whose text references a specific finding (named DC, named contact, named trigger event) — not generic phrasing (verified by string-match against fixture findings).
14. Clicking a suggested-question button results in the same downstream behavior as if the rep typed the question with `@`-mention (one `followup_question` WorkflowEvent + one bot reply).
15. If the suggested-questions LLM call fails, the brief still ships and a `suggested_questions_failed` event is logged.

### Move 4 — Diff-first cold-start
16. Given a snapshot saved 3 days ago for `"Walmart"` and a fresh DM `"Walmart"`, the bot replies with a diff front-door card that includes the saved_at date and three buttons.
17. Clicking `Re-research` fires the full pipeline (intent card → research).
18. Clicking `Dig into what's new` does NOT fire Exa, Apollo, or HubSpot calls (verified by patched clients).
19. Given a snapshot saved 20 days ago for an account, the bot ignores the snapshot and runs the full Move-1 flow.

### Foundation
20. After `handle_research_dm` runs, the resulting `Session` row has `thread_ts == message.ts` and `channel_id == message.channel`.

---

## 8. Open Questions for Sign-Off

1. **Snapshot freshness window** — 14 days as the default cutoff for "diff-first vs full re-research." Adjust based on observed account-change cadence.
2. **Intent types** — four buttons (`Outbound`, `Pre-call`, `Renewal`, `Just digging`) — is that the right vocabulary or should it be tighter to MC-BANTER stages?
3. **Suggested-question count** — 3 or 4? Three keeps the brief tight; four catches more dimensions.
4. **Rate limits** — 20/session/day and 100/rep/day. Defensible defaults; tune from telemetry.
5. **Disambiguation cache** — in-memory dict only, or persist to a `disambiguation_cache` table for cross-restart durability? In-memory is enough for V1.

If those five are acceptable as drafted, the TDD plan in `docs/v1-daily-use-tdd-plan.md` is the execution contract.

---

## 9. Out-of-Scope (V2/V3 placeholders)

- **V2:** ROI / cost-of-inaction doc generation (requires Alex sign-off so it doesn't cannibalize his heavyweight ROI tool). Email draft assembler. Asset library.
- **V3:** Apollo sequence enrollment (still gated by explicit AE click). UTM / attribution / meeting rate tracking. CRM write-back of artifacts to Salesforce or HubSpot.
- **Later:** Cross-account memory; streaming output; voice/image input; Slack-mention in non-DM channels.
