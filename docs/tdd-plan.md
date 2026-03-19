# TDD Execution Plan — Gather AI Prospecting Bot

Each phase satisfies two conditions: **(1)** completion verified by tests, **(2)** ends with a human-interpretable demo. Phases are sequential — each phase's tests must pass before the next begins. This is the last human checkpoint before autonomous execution starts.

---

## Phase Overview

| Phase | Deliverable | Key Test Criteria | Demo Definition |
|---|---|---|---|
| 1 | Slack bot + Input Normalizer | Schema validation, confidence scoring, clarification trigger | Rep sends free-form message → bot returns parsed intent card |
| 2 | Persona Discovery + Checkpoint 1 UI | Clay returns valid personas, Slack blocks render, rep approval gates next phase | Rep sees 8 persona cards, selects 3, hits confirm |
| 3 | Scorer + Value Mapper | Scoring rules produce correct tiers, value drivers map to correct personas | Persona cards update with scores, reasoning, and value driver |
| 4 | Sequence Generator | Lane routing correct, step structure matches template, personalization tokens filled | 3 approved personas → 3 draft sequences with lane differentiation |
| 5 | Edit Loop + Checkpoint 2 UI | Edits applied correctly, history tracked, step approval state managed | Rep edits subject line, bot updates, rep approves step by step |
| 6 | Sequence Brief Delivery + HubSpot Webhook | Brief formatting complete, all steps present, HubSpot updates on reply | Rep receives copy-paste-ready brief; HubSpot activity log updated |

---

## Phase 1 — Slack Bot + Input Normalizer

**Deliverable:** `NormalizedRequest`

**What gets built:**
- Slack bot connected to `#prospecting-bot` channel
- Input Normalizer agent that parses free-form rep message into structured intent
- Confirmation card sent back to rep
- Clarification flow if confidence < 0.7
- Rep role (`AE` / `MDR`) pulled from Slack user profile

**Test Criteria:**

| Test | Pass Condition |
|---|---|
| Valid account name extracted | `account_name` populated for any message containing a company name |
| Domain enrichment | `account_domain` returned for all accounts in HubSpot; fallback to Clay if not found |
| Persona filter extracted | `persona_filter` populated when rep mentions role ("VP Ops", "supply chain") |
| Use case angle extracted | `use_case_angle` populated when rep mentions context ("food safety", "compliance") |
| High confidence path | Input "Run outreach for Acme Corp" → `confidence ≥ 0.7`, no clarification triggered |
| Low confidence path | Input "do the thing for that logistics company" → `confidence < 0.7`, clarification question returned |
| Rep role routing | AE Slack user → `rep_role = "AE"`; MDR Slack user → `rep_role = "MDR"` |
| Confirmation card renders | Slack block card renders with account name, persona filter, angle, confirm/edit buttons |
| Edit flow works | Rep clicks ✏️ Edit → bot accepts correction → re-parses → returns updated card |

**Demo:** Rep sends *"Run outreach for Nestlé, targeting supply chain ops"* → bot replies with card: *"Got it — Nestlé. Targeting ODM + TDM personas. Supply chain ops angle. Is that right?"* → Rep clicks ✅ Yes.

---

## Phase 2 — Persona Discovery + Checkpoint 1 UI

**Deliverable:** `ScoredPersona[]` (pre-scoring, type/seniority only) + Slack persona card UI

**What gets built:**
- Clay API integration: search people by company + title keywords per persona type
- LinkedIn enrichment: scrape profile signals per persona
- Persona type classification logic (title → `TDM` / `ODM` / `FS` / `IT` / `Safety`)
- Seniority classification (title → seniority tier)
- Lane pre-assignment based on seniority
- Slack persona card blocks (one card per persona)
- Rep approval UI (checkboxes / approve buttons)
- State stored: approved personas gating Phase 3

**Test Criteria:**

| Test | Pass Condition |
|---|---|
| Clay returns results | At least 1 persona returned for any account with 5+ employees |
| Max 8 enforced | Never more than 8 personas returned regardless of Clay result count |
| Persona type classification | "Director of CI" → `TDM`; "COO" → `FS`; "VP IT" → `IT` — all title mappings correct |
| Seniority classification | "Director" → `Director`; "SVP" → `SVP`; "VP" → `VP` — all seniority mappings correct |
| Lane pre-assignment | All VP+ personas → `outreach_lane = "AE"`; Director/Manager → `outreach_lane = "MDR"` |
| LinkedIn signals fetched | At least signal type and relevance score returned per persona (empty array acceptable if no signals) |
| Slack cards render | All 8 persona cards render without error; sorted High type → Low type |
| Checkpoint 1 gates workflow | Clicking confirm with 0 personas selected → error message, workflow does not proceed |
| Approved personas stored | Selected personas persisted to session state; rejected personas excluded from all future phases |

**Demo:** After confirming Nestlé intent → bot returns 8 persona cards with name, title, type badge, lane badge, LinkedIn signal count. Rep unchecks IT and Safety personas, keeps 6, hits **Confirm & Generate Sequences**.

---

## Phase 3 — Scorer & Value Mapper

**Deliverable:** `ScoredPersona[]` with `priority_score`, `score_reasoning`, `value_driver`

**What gets built:**
- Google Drive integration: fetch account plan doc for target account
- Gong integration: fetch call transcripts for target account
- HubSpot integration: read contact and account activity history
- Scoring algorithm (default scores + elevation rules)
- Value driver assignment per persona type
- Gong hook extraction (relevant theme or quote per transcript)
- Account plan angle extraction
- Score reasoning generation (1–2 sentences per persona)

**Test Criteria:**

| Test | Pass Condition |
|---|---|
| Google Drive fetch | Account plan retrieved for accounts with existing Drive doc; graceful null if no doc found |
| Gong transcript fetch | Transcripts returned for accounts with call history; graceful null if no history |
| HubSpot history read | Account and contact activity returned; graceful null if no HubSpot record |
| Default scores correct | FS and TDM default to `High`; ODM to `Medium`; IT to `Medium`; Safety to `Low` |
| LinkedIn elevation | Persona with recent automation/inventory post within 90 days elevated one tier |
| Account plan elevation | Persona named in Google Drive account plan elevated one tier |
| C-Suite lock | C-Suite seniority persona always `High` regardless of type |
| Value driver mapping | TDM → `cycle_count_accuracy` or `labor_reduction`; FS → `network_roi` or `competitive_differentiation` — all mappings correct |
| Gong hook populated | `gong_hook` non-null for accounts with call transcripts; relevant to persona type |
| Score reasoning populated | All personas have non-empty `score_reasoning` string |
| Cards update in Slack | Persona cards re-render with score badge and value driver after Phase 3 completes |

**Demo:** 6 approved Nestlé personas now show updated cards: FS (COO) shows 🟢 High / `network_roi` / *"Q3 earnings call mentioned network inconsistency across DCs"*. TDM shows 🟢 High / `cycle_count_accuracy` / *"Named in account plan as primary CI contact."*

---

## Phase 4 — Sequence Generator

**Deliverable:** `Sequence[]` — one draft sequence per approved persona

**What gets built:**
- Sequence Generator agent (runs in parallel per approved persona)
- AE lane template + MDR lane template (step count, channel mix, day offsets — to be defined)
- Value driver → copy mapping (subject lines, body copy, CTA per driver)
- Gong hook injection into step 1 or 2
- Account plan angle injection
- Personalization token filling (`{{first_name}}`, `{{company}}`, `{{title}}`, etc.)
- Draft sequences stored to session state

**Test Criteria:**

| Test | Pass Condition |
|---|---|
| One sequence per approved persona | Sequence count equals approved persona count |
| AE lane structure | AE sequences match AE template (step count, channels, day offsets) |
| MDR lane structure | MDR sequences match MDR template |
| Value driver present in copy | Primary value driver messaging present in at least one step body |
| Gong hook injected | `gong_hook` appears in step 1 or 2 body when non-null |
| Personalization tokens filled | No unfilled `{{token}}` placeholders in any step |
| Subject lines present | All email steps have non-empty `subject_line` |
| No duplicate copy across personas | Step 1 body is unique per persona (not templated identically) |
| Sequences stored to state | All draft sequences retrievable by `sequence_id` |

**Demo:** 6 approved personas → bot posts 6 threaded sequence drafts. AE sequences (COO, SVP Ops) show 4-step email-heavy cadence with executive ROI framing. MDR sequences (Directors) show 6-step mixed email/LinkedIn cadence with operational pain framing.

---

## Phase 5 — Edit Loop + Checkpoint 2 UI

**Deliverable:** Fully approved `Sequence[]` with edit history

**What gets built:**
- Slack thread-based edit loop per sequence
- Natural language edit instruction → Sequence Editor agent → updated step
- Per-step approval (✅ approve / ✏️ edit)
- Full sequence approval when all steps approved
- Edit history tracked per step (`EditEvent[]`)
- Session state updated on each approval

**Test Criteria:**

| Test | Pass Condition |
|---|---|
| Edit instruction applied | "Change subject line to be more direct" → subject line updated in next message |
| Step-level edit | "Make step 2 shorter" → only step 2 updated; other steps unchanged |
| Sequence-level edit | "Regenerate the whole sequence with a compliance angle" → all steps regenerated |
| Edit history tracked | `EditEvent` recorded with `before`, `after`, `rep_instruction`, `timestamp` |
| Multiple edit rounds | Rep can edit same step multiple times; each round tracked separately |
| Approval state managed | Approved steps cannot be edited without explicit re-open |
| Partial approval | Rep can approve steps 1, 2, 3 independently; step 4 still in edit |
| Workflow gates on full approval | Sequence Brief not generated until all steps in a sequence are approved |
| Nothing ships without approval | Phase 6 cannot run while any sequence has `status ≠ "approved"` |

**Demo:** Rep reviews Nestlé COO sequence. Replies *"Drop the Mike reference in step 1"* → bot updates step 1 body. Rep replies *"Good, approve"* → step 1 locked. Repeat through all steps. Final sequence status = `approved`.

---

## Phase 6 — Sequence Brief Delivery + HubSpot Webhook

**Deliverable:** `SequenceBrief[]` delivered to rep + HubSpot activity update on reply

**What gets built:**
- Sequence Brief formatter (Slack blocks, copy-paste ready)
- One brief per approved sequence
- Optional: Google Doc export per persona
- HubSpot webhook receiver: Apollo reply data → HubSpot contact activity log

**Test Criteria:**

| Test | Pass Condition |
|---|---|
| Brief contains all approved steps | Step count in brief matches approved sequence step count |
| Copy-paste formatting | Subject lines and body copy clearly separated; no markdown artifacts |
| Personalization tokens absent | Zero `{{token}}` placeholders in delivered brief |
| AE/MDR label present | Lane clearly labeled on each brief |
| Persona summary present | Name, title, company, value driver shown at top of each brief |
| Google Doc export (optional) | Doc created with correct formatting if rep requests it |
| HubSpot webhook receives payload | Webhook endpoint returns 200 on valid Apollo payload |
| HubSpot contact updated | Activity log entry created on rep's HubSpot contact after sequence sent |
| Duplicate webhook prevention | Same payload sent twice → only one HubSpot activity created |

**Demo:** Rep sees 6 clean Sequence Briefs in Slack, one per persona, all copy-paste ready. Pastes COO sequence into Apollo, sends. Apollo reply data hits webhook → HubSpot contact activity shows *"Prospecting sequence sent via Gather AI bot."*

---

## Phase Dependency Map

```
Phase 1 ──► Phase 2 ──► Phase 3 ──► Phase 4 ──► Phase 5 ──► Phase 6
  │             │            │           │            │           │
Slack +     Clay +       Drive +     Sequence    Edit loop   Brief +
Normalizer  LinkedIn     Gong +      Generator   + approval  HubSpot
                         HubSpot     (parallel)
                         Scoring
```

Each phase produces an artifact that gates entry to the next. No phase begins until all tests in the prior phase pass and the demo is verified.

---

## Where Practitioner Effort Concentrates

Per the Context Engineering framework — human decisions required before autonomous execution begins:

| Area | Decision Needed Before Building |
|---|---|
| **Sequence templates** | AE vs MDR step count, channel mix, day offsets — gates Phase 4 |
| **Scoring weights** | Can reps adjust scoring weights per account? Or fixed rules only? |
| **Google Drive structure** | Are account plans in a consistent format/folder? How does the agent find the right doc? |
| **Gong access** | API key + does Gather AI have Gong transcripts for target accounts today? |
| **Clay setup** | Is Clay already connected? What's the enrichment credit budget per run? |
