# TDD Execution Plan: V1 Daily-Use Research Copilot

**Spec:** `docs/v1-daily-use-spec.md`
**Supersedes:** `docs/followup-qa-tdd-plan.md`
**Phases:** 7 — each gates on its own passing tests before the next starts.
**Test runner:** `pytest` + `pytest-mock` + `pytest-asyncio` (already in `requirements.txt`).
**Test directory:** `tests/phase16/` (continues the existing phase-N numbering).

---

## Build Order Rationale

Order de-risks dependencies and ships value in chunks:

1. **Foundation** — without `Session.thread_ts` persisted and a session-lookup helper, Move 2 and Move 4 can't match anything.
2. **Move 4 (diff-first)** — biggest UX win for daily use, smallest build (leverages existing `memory.snapshots` + `memory.diff`). Ships before Q&A so reps have a reason to come back even before Q&A is live.
3. **Move 1 (intent capture)** — independent UX add; lifts research quality across the board.
4. **Move 3 (suggested follow-ups)** — depends on Q&A pipeline for the click-through, so build the agent first and wire the click in Phase 6.
5. **Move 2 (Q&A) — Phases 5 & 6** — the load-bearing piece. Split into agent (5) + handler integration (6).
6. **End-to-end smoke** — Phase 7.

---

## Phase 1 — Foundation: session lookup + thread_ts persistence

**Build:**
- Add `get_session_by_thread_ts(thread_ts: str, rep_id: str) -> Optional[Session]` to `src/research/sessions.py`. Returns the most recent non-cancelled session matching both keys.
- In `src/handlers/dm_research.py`, immediately after `create_session(...)`, persist `session.thread_ts = message.get("ts")` and `session.channel_id = message.get("channel")`. Commit.

**Tests (`tests/phase16/test_session_foundation.py`):**
1. `test_lookup_returns_session_for_matching_thread_and_rep`
2. `test_lookup_returns_none_for_wrong_rep`
3. `test_lookup_returns_none_for_unknown_thread_ts`
4. `test_lookup_skips_cancelled_sessions`
5. `test_lookup_returns_most_recent_if_multiple`
6. `test_dm_research_persists_thread_ts_and_channel`

**Gate:** All 6 pass.

---

## Phase 2 — Move 4: diff-first cold-start

**Build:**
- New `src/handlers/diff_front_door.py` with `should_show_diff_front_door(account_name) -> Optional[snapshot]` (returns the snapshot if < `SNAPSHOT_FRESHNESS_DAYS` old; else None).
- New `diff_front_door_card(snapshot, account_name) -> blocks` in `src/integrations/slack_blocks.py`. Three buttons: `re_research`, `dig_into_new`, `ask_specific`.
- In `dm_research.handle_research_dm`, before calling `run_account_research`, check `should_show_diff_front_door`. If a snapshot is found, post the front-door card and return — do NOT run research.
- Wire button handlers in `src/main.py`:
  - `re_research` → falls through to the existing research path (Move 1 in Phase 3 will wrap this).
  - `dig_into_new` → loads the prior `CompanyResearch` (via `prior_company_research_id` on the new session) and posts the brief card.
  - `ask_specific` → posts a hint message in the thread.
- Add env var `SNAPSHOT_FRESHNESS_DAYS` (default 14) in `src/config.py`.

**Tests (`tests/phase16/test_diff_front_door.py`):**
1. `test_should_show_returns_snapshot_when_fresh` — snapshot 3 days old, returns it.
2. `test_should_show_returns_none_when_stale` — 20 days old, returns None.
3. `test_should_show_returns_none_when_no_snapshot` — never researched, returns None.
4. `test_freshness_threshold_respects_env_var` — set `SNAPSHOT_FRESHNESS_DAYS=7`, a 10-day-old snapshot returns None.
5. `test_card_includes_saved_at_date` — rendered blocks contain a human-readable date string.
6. `test_card_has_three_action_buttons` — `re_research`, `dig_into_new`, `ask_specific` action_ids present.
7. `test_handle_research_dm_short_circuits_on_fresh_snapshot` — given a fresh snapshot, `say` is called with the diff card and `run_account_research` is NOT called.
8. `test_handle_research_dm_runs_research_on_stale_snapshot` — given a stale snapshot, full research runs.
9. `test_dig_into_new_loads_prior_research_no_api_calls` — patch Exa/Apollo/HubSpot to raise; clicking the button must complete without invoking any of them.
10. `test_re_research_button_starts_fresh_pipeline` — patch the pipeline entry and assert it's called once.

**Gate:** All 10 pass.

---

## Phase 3 — Move 1: intent capture

**Build:**
- New `src/handlers/intent_capture.py`:
  - `is_account_ambiguous(account_name) -> Optional[List[Dict]]` — one-shot LLM call (Haiku) returning disambiguation options or None. Cached in module-level dict keyed by lowercased account name.
  - `intent_capture_card(account_name, disambiguation_options) -> blocks` — Slack Block Kit card.
- In `dm_research.handle_research_dm`: after the diff-front-door check (Phase 2) but before `run_account_research`, post the intent card and return. Research starts only after the rep submits intent.
- New `submit_intent` action handler that persists `disambiguation` + `intent_type` to `Session.normalized_request`, logs `intent_captured`, and fires `run_account_research`.
- Update `src/research/runner.py` to accept the captured intent and thread it into the agent prompts (existing `normalized_request` plumbing already supports this — extend the prompts in `src/agents/researcher.py`, `src/agents/sales_play.py` to read `intent_type`).

**Tests (`tests/phase16/test_intent_capture.py`):**
1. `test_ambiguity_check_returns_options_for_volvo` — mock LLM to return 2 Volvo options; helper returns the list.
2. `test_ambiguity_check_returns_none_for_sysco` — mock LLM to return null; helper returns None.
3. `test_ambiguity_check_caches_per_account` — second call with same name does NOT hit LLM (patch httpx and assert call count == 1).
4. `test_ambiguity_check_handles_llm_failure` — LLM raises → helper returns None, logs the failure.
5. `test_card_includes_disambiguation_when_options_present`
6. `test_card_omits_disambiguation_when_none`
7. `test_card_always_includes_four_intent_buttons`
8. `test_handle_research_dm_posts_intent_card_when_no_snapshot`
9. `test_handle_research_dm_does_not_run_research_until_intent_submitted`
10. `test_submit_intent_persists_to_normalized_request` — clicking submits both selections to DB.
11. `test_submit_intent_logs_event`
12. `test_submit_intent_fires_run_account_research_with_intent` — assert `run_account_research` called and `session.normalized_request.intent_type` is in the args.
13. `test_research_agent_prompt_includes_intent_emphasis` — patch httpx, fire research with `intent_type="outbound"`, assert the prompt body contains an outbound emphasis line.

**Gate:** All 13 pass.

---

## Phase 4 — Move 2 agent: context builder + OpenRouter call

**Build:**
- New `src/agents/followup_agent.py`:
  - `build_followup_context(session, company_research, personas, contact_researches, thread_history, question) -> str` — pure function.
  - `answer_followup(context) -> str` — OpenRouter call, mirrors `src/agents/researcher.py:336` pattern. `max_tokens=800`, `temperature=0.3`, `timeout=30`. Narrow exception handling; fallback string on any failure.

**Tests (`tests/phase16/test_followup_agent.py`):**
1. `test_context_includes_account_name`
2. `test_context_includes_facility_count`
3. `test_context_includes_persona_names_and_titles`
4. `test_context_includes_flagged_contact_research`
5. `test_context_truncates_raw_research_to_12k`
6. `test_context_includes_thread_history_oldest_first`
7. `test_context_caps_total_size_at_28k`
8. `test_context_includes_current_question_verbatim`
9. `test_context_handles_no_personas`
10. `test_context_handles_no_thread_history`
11. `test_answer_returns_llm_content_on_success`
12. `test_answer_handles_timeout`
13. `test_answer_handles_non_2xx`
14. `test_answer_handles_malformed_json`
15. `test_answer_uses_configured_model`
16. `test_answer_never_calls_exa_apollo_hubspot` — patch all three clients to raise; `answer_followup` must complete without calling any.
17. `test_answer_does_not_log_api_keys`

**Gate:** All 17 pass.

---

## Phase 5 — Move 2 handler: routing + Slack integration

**Build:**
- Resolve and cache bot user ID at startup via `client.auth_test()`.
- New `src/handlers/followup_qa.py` with `is_bot_mentioned(text, bot_user_id) -> bool` and `handle_followup(message, say, client)`.
- Modify `dm_research.handle_research_dm` to branch on `thread_ts`:
  - Thread reply + session match + mention → `handle_followup`.
  - Thread reply + session match + no mention → silent ignore.
  - Thread reply + no session match → silent ignore (do NOT fall through to new-research).
  - Top-level DM → existing path (intent + diff-front-door from Phases 2 & 3).
- `handle_followup` flow:
  1. Post "thinking…" placeholder.
  2. Authz check (`message.user == session.rep_id`).
  3. Rate-limit check (20/session/24h, 100/rep/24h via SQL count on `WorkflowEvent`).
  4. Load session + company_research + personas + contact_researches.
  5. Pull `conversations.replies(thread_ts, limit=20)`.
  6. Build context (Phase 4) → call `answer_followup` (Phase 4) → `safe_mrkdwn` the result.
  7. `chat_update` placeholder with final block (answer + footer). Fall back to `chat_postMessage` if update fails.
  8. Log `WorkflowEvent(event_type="followup_question", payload={question_length, answer_length, latency_ms})`.

**Tests (`tests/phase16/test_followup_handler.py`):**
1. `test_mention_detection_finds_bot_id`
2. `test_mention_detection_ignores_wrong_id`
3. `test_mention_detection_handles_no_mention`
4. `test_thread_reply_with_mention_routes_to_handle_followup`
5. `test_thread_reply_without_mention_is_ignored`
6. `test_thread_reply_unknown_session_is_ignored` — does NOT start new research.
7. `test_top_level_dm_unchanged` — Phase 2/3 paths still fire.
8. `test_thread_reply_wrong_rep_is_ignored` — `forbidden_followup` logged.
9. `test_handler_posts_placeholder_within_3s` — placeholder posted before any LLM call.
10. `test_handler_edits_placeholder_with_answer`
11. `test_handler_falls_back_to_post_on_update_failure`
12. `test_handler_runs_answer_through_safe_mrkdwn` — inject `<http://evil|click>`; output stripped.
13. `test_handler_logs_workflow_event_with_latency`
14. `test_handler_does_not_log_raw_question_text`
15. `test_handler_handles_research_still_running`
16. `test_handler_handles_cancelled_session`
17. `test_handler_handles_empty_question_after_mention`
18. `test_handler_respects_per_session_rate_limit` — 20 prior events → 21st returns rate-limit message.
19. `test_handler_respects_per_rep_rate_limit` — 100 prior events → next returns rate-limit message.
20. `test_handler_footer_line_present`

**Gate:** All 20 pass.

---

## Phase 6 — Move 3: suggested follow-ups

**Build:**
- New `src/agents/suggested_questions.py` with `generate_suggested_questions(company_research, personas) -> List[str]`. One LLM call. Validates returned JSON array. Falls back to 3 generic questions on failure (and logs `suggested_questions_failed`). Timeout: 5s.
- New `suggested_questions_block(questions) -> blocks` in `slack_blocks.py`. One Slack `actions` block with 3–4 buttons. Each button: `action_id="suggested_question"`, `value` = question text.
- Modify `src/research/runner.py` (or the brief render path) to call the generator and append the block after the brief.
- New action handler `suggested_question` in `src/main.py`. Synthesizes a fake message dict with `text="<@BOT> {question}"`, `user=clicker_user_id`, `thread_ts=session.thread_ts`, then routes through `handle_followup` (Phase 5) so the answer pipeline is identical to a typed mention.

**Tests (`tests/phase16/test_suggested_questions.py`):**
1. `test_generator_returns_3_or_4_questions_on_success`
2. `test_generator_questions_reference_specific_findings` — mock LLM to return questions, assert each contains a named entity from the fixture findings (DC name, contact name, or trigger event verb).
3. `test_generator_falls_back_on_json_parse_failure` — returns 3 generic questions; logs `suggested_questions_failed`.
4. `test_generator_falls_back_on_timeout`
5. `test_generator_respects_5s_timeout` — patch httpx to sleep 6s; generator returns fallback within ~5s.
6. `test_block_renders_one_button_per_question`
7. `test_block_action_ids_are_suggested_question`
8. `test_brief_includes_suggested_questions_block` — after `run_account_research`, the posted blocks contain the actions block.
9. `test_brief_ships_without_block_if_generator_fails` — generator raises; brief still posts; logs `suggested_questions_slow` or `_failed`.
10. `test_suggested_question_click_routes_through_handle_followup` — patch `handle_followup`, click a button, assert called once with a synthesized message dict.
11. `test_suggested_question_click_logs_event` — `suggested_question_clicked` event with the question text.
12. `test_suggested_question_click_respects_rate_limit` — at the rate-limit ceiling, click returns rate-limit message; no LLM call.

**Gate:** All 12 pass.

---

## Phase 7 — End-to-end integration

**Build:** No new code. Wire seeded fixtures and run the canonical flow from spec §3 against the full stack.

**Tests (`tests/phase16/test_e2e_v1.py`):**
1. `test_e2e_first_time_account` — DM `"Volvo"` (no snapshot, ambiguous) → intent card with disambiguation + 4 intent buttons → click `Volvo Group` + `Outbound` → research runs with intent passed → brief posts with suggested-questions block → assert all four artifacts appear in correct order.
2. `test_e2e_repeat_account_diff_front_door` — seed snapshot 3 days old; DM `"Walmart"` → diff front-door card posted → research NOT run → click `Dig into what's new` → brief from prior research posted; no new external API calls.
3. `test_e2e_followup_question_after_brief` — after a brief lands, fire a thread reply with `<@BOT> who first?` → placeholder posted → final answer references a real persona from fixtures → `WorkflowEvent` logged.
4. `test_e2e_suggested_question_click_equals_typed_question` — click a suggested-question button; assert downstream events and final post are equivalent to typing the same question with `@`-mention.
5. `test_e2e_unauthorized_user_silent` — different rep clicks/replies → no Slack post, `forbidden_followup` logged.
6. `test_e2e_no_fresh_retrieval_anywhere_in_qa_path` — patch Exa, Apollo, HubSpot to raise on any call; full Q&A flow completes successfully.
7. `test_e2e_research_passes_intent_to_agents` — fire intent submit with `intent_type="renewal"`; patch httpx; assert the agent prompt body contains renewal-specific emphasis.

**Gate:** All 7 pass.

---

## Manual Smoke (after Phase 7 + Railway deploy)

Walk through spec §3's canonical flow in real Slack:

1. DM `Volvo` → confirm disambiguation + intent card.
2. Click `Volvo Group` + `Outbound first-touch` → confirm research runs, brief lands with 3–4 suggested-question buttons.
3. Click a suggested question → confirm grounded answer + footer line.
4. Wait, then DM `Walmart` (seed a recent snapshot first) → confirm diff front-door card.
5. Click `Re-research` → confirm intent card → research → brief.
6. In a research thread, type `@bot which contact first?` → confirm answer references a real persona.
7. Have a teammate `@`-mention the bot in the same thread → confirm no response, event logged.
8. Send `@bot` alone → confirm hint message.
9. Spam 21 follow-ups → confirm 21st returns rate-limit message.

Record outcomes in `docs/v1-daily-use-demo-review.md` before any team-wide release.

---

## Observability

Per spec, every significant event writes a `WorkflowEvent` row. Useful queries for ongoing visibility:

```sql
-- Daily active reps
SELECT DATE(timestamp), COUNT(DISTINCT rep_id)
FROM workflow_events
WHERE event_type IN ('intent_captured', 'followup_question', 'diff_front_door_shown')
GROUP BY 1 ORDER BY 1 DESC;

-- Q&A latency p95 (last 7 days)
SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY (payload->>'latency_ms')::int)
FROM workflow_events
WHERE event_type = 'followup_question'
  AND timestamp > now() - interval '7 days';

-- Suggested-question click-through
SELECT
  COUNT(*) FILTER (WHERE event_type = 'suggested_question_clicked')::float
  / NULLIF(COUNT(*) FILTER (WHERE event_type = 'research_complete'), 0)
FROM workflow_events
WHERE timestamp > now() - interval '7 days';
```

The third one is the key daily-use metric: how often a brief turns into a follow-up. Target ≥ 0.6 in steady state.
