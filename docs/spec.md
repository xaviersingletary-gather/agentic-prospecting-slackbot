# Gather AI Prospecting Bot — Specification Artifact

## Overview

A multi-agent Slack bot that takes a free-form rep message about a target account and orchestrates persona discovery, enrichment, scoring, value mapping, and sequence generation — with human-in-the-loop checkpoints at every phase. Output is a ready-to-paste sequence brief delivered in Slack.

---

## User Stories

| Actor | Action | Measurable Outcome |
|---|---|---|
| Rep | Sends free-form Slack message with account name | System initiates workflow and confirms intent within 10 seconds |
| Rep | Reviews parsed intent before workflow runs | Can correct account name, persona filter, or angle before agents spin up |
| Rep | Reviews ranked persona list with scores and reasoning | Confirms or rejects individual personas before any sequence is generated |
| Rep | Edits sequence steps via natural language in Slack thread | Updated step reflected in next bot message |
| Rep | Approves final sequence | Bot delivers formatted Sequence Brief ready to paste into Apollo |
| System | Receives reply data from Apollo | HubSpot contact/activity record updated automatically via webhook |

---

## Explicitly Excluded (Non-Goals for v1)

- No batch multi-account runs in a single session (one account per conversation)
- No auto-sending — nothing reaches a prospect without rep approval
- No manager approval layer
- No LinkedIn DM sending (bot writes copy, rep executes manually)
- No creating new Apollo contacts via API
- No Apollo sequence creation via API (copy-paste workflow instead)
- No Gong transcript fetching for accounts with zero call history
- No ICP guardrail enforcement (honor system in v1)
- No multi-user / team-level session sharing

---

## Data Models

### `RepRequest`
Raw Slack input. Never passed directly to agents.
```
account_name:   string        // required
raw_message:    string        // original Slack text verbatim
rep_id:         string        // Slack user ID
rep_role:       "AE" | "MDR"  // pulled from Slack user profile
channel_id:     string
timestamp:      datetime
```

### `NormalizedRequest`
Output of the Input Normalizer agent. Gates entry to the rest of the workflow.
```
account_name:             string
account_domain:           string              // enriched if not in raw message
company_description:      string | null       // 1-line from HubSpot or Clay
persona_filter:           PersonaType[] | null  // ["TDM","FS"] if rep specified
use_case_angle:           string | null       // e.g. "food safety compliance"
rep_role:                 "AE" | "MDR"
confidence:               float               // 0–1; below 0.7 triggers clarification
clarification_needed:     boolean
clarification_question:   string | null
```

### `Persona`
Raw persona record from Clay enrichment.
```
persona_id:     string
first_name:     string
last_name:      string
title:          string
seniority:      "C-Suite" | "SVP" | "VP" | "Director" | "Manager"
persona_type:   "TDM" | "ODM" | "FS" | "IT" | "Safety"
linkedin_url:   string | null
email:          string | null
account_name:   string
account_domain: string
```

### `LinkedInSignal`
```
type:              "recent_post" | "job_change" | "announcement" | "project" | "skill"
content:           string
date:              date | null
relevance_score:   float   // 0–1
```

### `ValueDriver`
```
primary_driver:       ValueDriverEnum
gong_hook:            string | null   // specific theme or quote from Gong transcript
account_plan_angle:   string | null   // relevant context from Google Drive account plan
messaging:            string          // 1–2 sentence personalized angle for this persona
```

**`ValueDriverEnum`**
```
"cycle_count_accuracy"
"labor_reduction"
"shrink_reduction"
"network_roi"
"compliance_traceability"
"wms_integration_stability"
"throughput_preservation"
"competitive_differentiation"
```

### `ScoredPersona`
All `Persona` fields plus:
```
priority_score:     "High" | "Medium" | "Low"
score_reasoning:    string          // 1–2 sentences shown to rep in Slack
outreach_lane:      "AE" | "MDR"   // VP+ = AE; Director/Manager = MDR
linkedin_signals:   LinkedInSignal[]
value_driver:       ValueDriver
approved_by_rep:    boolean | null  // null = pending rep review
```

### `SequenceStep`
```
step_number:     int
channel:         "email" | "linkedin" | "call"
day_offset:      int              // days from sequence start
subject_line:    string | null    // email only
body:            string
approved:        boolean | null
```

### `Sequence`
```
sequence_id:          string
persona_id:           string
lane:                 "AE" | "MDR"
steps:                SequenceStep[]
status:               "draft" | "rep_review" | "approved" | "delivered"
edit_history:         EditEvent[]
```

### `EditEvent`
```
timestamp:        datetime
rep_instruction:  string        // free-form Slack message
step_number:      int | null    // null = applies to whole sequence
before:           string
after:            string
```

### `SequenceBrief`
Final output delivered to rep. No API push — formatted for copy-paste into Apollo.
```
sequence_id:       string
persona:           ScoredPersona (summary)
lane:              "AE" | "MDR"
steps:             SequenceStep[]
delivery_format:   "slack_blocks" | "google_doc"
delivered_at:      datetime
```

---

## Persona Scoring Logic

### Default Scores by Persona Type

| Persona Type | Default Score | Rationale |
|---|---|---|
| FS (Executive) | **High** | Budget owner; late engagement = #1 Closed Lost pattern |
| TDM (Technical Lead) | **High** | Primary champion; initiates and builds internal momentum |
| ODM (Operations Lead) | **Medium** | Critical for adoption; enters via TDM referral |
| IT | **Medium** | Active gating role in new logo deals |
| Safety | **Low** | Situational; regulated verticals only |

### Score Elevation Rules
- LinkedIn signal with post about automation, inventory, or ops within 90 days → elevate one tier
- Persona is named contact in Google Drive account plan → elevate one tier
- C-Suite or SVP seniority on any persona → lock at High regardless of type

### Lane Routing
```
outreach_lane = "AE"   if seniority in ["C-Suite", "SVP", "VP"]
outreach_lane = "MDR"  if seniority in ["Director", "Manager"]
```

### Persona Title Keywords (for Clay search)

| Type | Title Keywords |
|---|---|
| TDM | Head of CI, Director of CI, Industrial Engineer, Automation Manager, Director of Engineering & Automation, VP Engineering, Director of Continuous Improvement |
| ODM | Director of Operations, VP Warehouse & Distribution, Director of Inventory & Supply Chain, Director of Warehouse Operations, Inventory Manager, VP Operations, VP Supply Chain |
| FS | CSCO, COO, EVP Operations, SVP Operations, EVP Supply Chain, SVP Supply Chain, Head of Network Operations, President |
| IT | VP Information Technology, Manager IT Systems, IT Business Systems Integration Manager, Director Enterprise Systems |
| Safety | VP Risk Management, EHS Manager, Director of Safety, VP Risk |

**Max personas returned:** 8 per account, sorted High → Medium → Low

---

## Value Driver → Persona Mapping

| Persona | Primary Value Driver | Hook Source |
|---|---|---|
| TDM | `cycle_count_accuracy`, `wms_integration_stability`, `labor_reduction` | Gong themes + account plan |
| ODM | `shrink_reduction`, `throughput_preservation`, `labor_reduction` | Gong themes |
| FS | `network_roi`, `competitive_differentiation` | Account plan goals + earnings call signals |
| IT | `wms_integration_stability`, `compliance_traceability` | None (use standard SOC 2 / integration messaging) |
| Safety | `compliance_traceability` | None (use standard OSHA / safety messaging) |

---

## Agent Architecture

```
[Slack: free-form rep message]
            │
            ▼
┌─────────────────────────────┐
│  Agent 1: Input Normalizer  │
│  Tools: HubSpot, Clay       │
│  Output: NormalizedRequest  │
│  Gate: confidence < 0.7     │◄── asks rep clarification if needed
└────────────┬────────────────┘
             │
             ▼
┌──────────────────────────────────┐
│  Agent 2: Persona Discovery      │
│  Tools: Clay (people search),    │
│         LinkedIn scraper         │
│  Output: Persona[] (max 8)       │
└────────────┬─────────────────────┘
             │
             ▼
┌──────────────────────────────────────────────┐
│  Agent 3: Scorer & Value Mapper              │
│  Tools: Google Drive (account plan),         │
│         Gong (call transcripts),             │
│         HubSpot (contact/account history)    │
│  Output: ScoredPersona[] sorted by score     │
└────────────┬─────────────────────────────────┘
             │
             ▼
     ┌───────────────┐
     │  CHECKPOINT 1 │  Rep reviews persona cards in Slack
     │  Rep approves │  Approves / rejects each persona
     │  or rejects   │  Nothing proceeds until confirmed
     └───────┬───────┘
             │
             ▼  (runs in parallel per approved persona)
┌──────────────────────────────────────┐
│  Agent 4: Sequence Generator         │
│  Tools: Messaging framework,         │
│         Value driver library,        │
│         Persona + account context    │
│  Output: Sequence (draft) per        │
│          approved persona            │
└────────────┬─────────────────────────┘
             │
             ▼
     ┌───────────────┐
     │  CHECKPOINT 2 │  Rep reviews each step in Slack thread
     │  Edit loop    │  Free-form natural language edits
     │               │  Bot regenerates, rep re-reviews
     │               │  Repeats until rep types "approve"
     └───────┬───────┘
             │
             ▼
┌──────────────────────────────────┐
│  Agent 5: Sequence Brief Delivery│
│  Tools: Slack blocks formatter,  │
│         Google Docs (optional)   │
│  Output: SequenceBrief per       │
│          approved persona        │
└────────────┬─────────────────────┘
             │
             ▼
     [Rep copy-pastes into Apollo]
             │
             ▼
     [Apollo reply data → HubSpot via webhook]
```

---

## Slack Interaction Definitions

### Phase 1 — Trigger & Confirmation
- Rep sends free-form message in designated channel (e.g. `#prospecting-bot`)
- Bot replies with parsed intent card:
  > *"Got it — running outreach for **Acme Corp**. Targeting TDM + FS personas. 3PL compliance angle. Is that right?"*
- Rep: ✅ **Yes, run it** / ✏️ **Edit**
- If confidence < 0.7, bot asks a single clarification question before proceeding

### Phase 2 — Persona Review
- Bot posts up to 8 persona cards as Slack blocks, sorted High → Medium → Low
- Each card shows:
  - Name, Title, Company
  - Score badge (🟢 High / 🟡 Medium / 🔴 Low)
  - Outreach lane (AE / MDR)
  - Value driver
  - 1-line score reasoning
- Rep selects personas via checkboxes or buttons
- Rep hits **✅ Confirm & Generate Sequences**
- Nothing proceeds until rep confirms

### Phase 3 — Sequence Review & Edit Loop
- Bot posts sequence steps per persona as threaded Slack messages
- Each step shows: Step #, Channel, Day offset, Subject line (if email), Body copy
- Rep replies in thread with natural language:
  - *"Change the subject line"*
  - *"Make step 2 shorter"*
  - *"Drop the Mike reference"*
  - *"Regenerate the whole thing with a compliance angle"*
- Bot applies edit, posts updated step
- Rep types **"approve"** or clicks ✅ to finalize each step
- Full sequence approved when all steps are approved

### Phase 4 — Sequence Brief Delivery
- Bot posts final Sequence Brief per persona as formatted Slack blocks
- Each step is copy-paste ready (subject + body clearly separated)
- Bot message:
  > *"Here's your sequence for [Name] at [Company]. Ready to paste into Apollo. 🚀"*
- Rep copies into Apollo manually

---

## Integration Contracts

| Integration | Agent | Usage | Direction |
|---|---|---|---|
| Slack | All agents | Receive rep messages, post responses, interactive blocks | Bidirectional |
| HubSpot | Input Normalizer, Scorer | Account lookup, contact history, activity update | Read + Write |
| Clay | Persona Discovery | Find people by company + title keywords, enrich contact data | Read |
| LinkedIn | Persona Discovery, Scorer | Scrape profile, recent posts, signals | Read |
| Google Drive | Scorer | Fetch account plan for target account | Read |
| Gong | Scorer | Fetch call transcripts for account | Read |
| Apollo | Delivery | No API — copy-paste workflow (v1) | None (v1) |

---

## Open Items (Deferred)

1. **Sequence templates** — Step count, channel mix (email/LinkedIn/call), and day offsets for AE lane vs. MDR lane. Defines the Sequence Generator's execution contract.
2. **Apollo upgrade path** — When to swap copy-paste for a real integration (Outreach/Salesloft as alternatives worth evaluating).
