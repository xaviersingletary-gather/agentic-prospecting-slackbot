# Gather AI Prospecting Bot — Specification Artifact

## Overview

A multi-agent Slack bot that takes a free-form rep message about a target account and orchestrates a three-layer research and outreach pipeline: company research (10-Ks, earnings calls, press releases, trigger events), contact sourcing (Apollo), individual contact research, and sequence generation — with human-in-the-loop checkpoints at every phase. Output is a ready-to-paste sequence brief delivered in Slack.

Nothing reaches a prospect without rep approval.

---

## User Stories

| Actor | Action | Measurable Outcome |
|---|---|---|
| Rep | Sends free-form Slack message with account name | System initiates workflow and confirms intent within 10 seconds |
| Rep | Reviews parsed intent before workflow runs | Can correct account name or angle before research begins |
| Rep | Watches company research progress in real time | Single Slack message updates live as each research step completes |
| Rep | Reviews company research brief | Sees DCs, board initiatives, trigger events, Exception Tax estimate — with explicit gaps flagged |
| Rep | Approves research and triggers contact sourcing | Apollo pull runs only after rep confirms the company brief |
| Rep | Reviews contact list and flags up to 3 for deep research | Removes unwanted contacts; bot enforces 3-contact deep research cap |
| Rep | Reviews individual research cards for flagged contacts | Sees tenure, prior roles, recent LinkedIn activity per flagged contact |
| Rep | Reviews and edits generated sequences | Natural language edits applied per step; bot regenerates and rep re-reviews |
| Rep | Approves final sequences | Bot delivers formatted Sequence Brief ready to paste into Apollo |
| System | Receives reply data from Apollo | HubSpot contact/activity record updated automatically via webhook |

---

## Explicitly Excluded (Non-Goals for v1)

- No batch multi-account runs in a single session (one active account per rep at a time)
- No auto-sending — nothing reaches a prospect without rep approval
- No manager approval layer
- No LinkedIn DM sending (bot writes copy, rep executes manually)
- No creating new Apollo contacts via API
- No Apollo sequence creation via API (copy-paste workflow instead)
- No Gong transcript fetching for accounts with zero call history
- No ICP guardrail enforcement (honor system in v1)
- No multi-user / team-level session sharing
- No full 10-K ingestion — targeted section extraction only (MD&A, Risk Factors, capex mentions)
- No paywalled earnings transcript sources (Seeking Alpha free tier + company IR pages only)

---

## Session State Machine

Every workflow is persisted in the DB with a `phase` field. On any rep message, the bot checks for an active session and resumes from the current phase.

```
company_identified
→ research_in_progress
→ research_complete
→ research_approved
→ contacts_sourcing
→ contacts_sourced
→ contacts_approved
→ individual_research_in_progress
→ individual_research_complete
→ sequences_generating
→ sequences_draft
→ sequences_approved
→ complete
```

If a rep tries to start a new account while a session is active, the bot blocks and asks: *"You have an active [Company] session. Want to continue it, or cancel and start fresh?"*

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
is_public_company:        boolean | null      // determines EDGAR availability
ticker_symbol:            string | null       // for EDGAR lookup if public
persona_filter:           PersonaType[] | null  // ["TDM","FS"] if rep specified
use_case_angle:           string | null       // e.g. "food safety compliance"
rep_role:                 "AE" | "MDR"
confidence:               float               // 0–1; below 0.7 triggers clarification
clarification_needed:     boolean
clarification_question:   string | null
```

### `AccountDocument`
A fetched and parsed external document (10-K, press release, earnings transcript).
```
document_id:      string
account_name:     string
doc_type:         "10-K" | "10-Q" | "earnings_transcript" | "press_release" | "investor_presentation" | "news_article"
source_url:       string
fetched_at:       datetime
format:           "html" | "pdf"
relevant_sections: string[]   // extracted text from targeted sections only
filing_period:    string | null  // e.g. "FY2024", "Q3 2025"
```

### `CompanyResearch`
Output of the Company Researcher agent. Stored in DB and shown to rep at Checkpoint 1.
```
session_id:           string
account_name:         string
is_public_company:    boolean
facility_count:       int | null
facility_count_note:  string | null     // "estimated" or source citation
total_sqft_estimate:  int | null        // estimated total warehouse square footage
board_initiatives:    BoardInitiative[] // 2–3 items, sourced
company_priorities:   string[]          // 1–2 operational priorities
trigger_events:       TriggerEvent[]    // 1–2 specific, recent
automation_vendors:   AutomationVendor[]
exception_tax:        ExceptionTaxEstimate
research_gaps:        string[]          // data points that could not be found
documents_used:       AccountDocument[]
researched_at:        datetime
```

### `BoardInitiative`
```
title:    string
summary:  string   // 1 sentence
source:   string   // e.g. "Q3 2025 earnings call", "2024 10-K MD&A"
```

### `TriggerEvent`
```
description:  string   // specific, concrete event
source:       string
date:         date | null
relevance:    string   // 1 sentence on why this matters for Gather
```

### `AutomationVendor`
```
vendor_name:    string
category:       string   // e.g. "WMS", "robotics", "inventory tech"
deployment_status: "deployed" | "piloting" | "rumored"
source:         string | null
```

### `ExceptionTaxEstimate`
Calculated deterministically from facility data. Not LLM-generated.
```
total_sqft:           int
pallet_positions:     int      // totalSqFt × 0.60 × 4 / 36
annual_savings_usd:   float    // positions × 0.025 × 4 × $100 × 0.80
annual_savings_mm:    float    // above / 1,000,000
math_shown:           string   // human-readable formula string for rep review
sqft_source:          string   // "public" | "estimated from X facilities × Y avg sqft"
```

### `Persona`
Contact record sourced from Apollo.
```
persona_id:               string
first_name:               string
last_name:                string
title:                    string
seniority:                "C-Suite" | "SVP" | "VP" | "Director" | "Manager"
persona_type:             "TDM" | "ODM" | "FS" | "IT" | "Safety"
linkedin_url:             string | null
email:                    string | null
account_name:             string
account_domain:           string
deep_research_flagged:    boolean   // rep flagged for individual research
approved_by_rep:          boolean | null
```

### `ContactResearch`
Output of Individual Researcher agent. Only produced for rep-flagged contacts (max 3).
```
persona_id:           string
current_role_tenure:  string | null      // e.g. "2 years"
prior_roles:          PriorRole[]        // last 1–2 roles only
recent_linkedin:      LinkedInSignal[]   // 1–2 recent posts or statements
speaking_activity:    string | null      // conference talk, quote, interview if found
research_gaps:        string[]
researched_at:        datetime
```

### `PriorRole`
```
title:        string
company:      string
duration:     string | null
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
company_research_hook: string | null  // specific finding from CompanyResearch that grounds this persona's angle
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
priority_score:       "High" | "Medium" | "Low"
score_reasoning:      string            // 1–2 sentences shown to rep in Slack
outreach_lane:        "AE" | "MDR"
linkedin_signals:     LinkedInSignal[]
value_driver:         ValueDriver
contact_research:     ContactResearch | null   // populated only for flagged contacts
personalization_tier: "deep" | "standard"      // deep = flagged + researched; standard = persona type only
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
personalization_tier: "deep" | "standard"
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
┌──────────────────────────────────────────────────┐
│  Agent 2: Company Researcher                     │
│  Tools:                                          │
│    search_web (Exa)                              │
│    search_edgar (SEC EDGAR API)                  │
│    fetch_url (HTML documents)                    │
│    fetch_document (PDF download + text extract)  │
│    extract_sections (target MD&A, Risk Factors,  │
│                      capex mentions)             │
│  Output: CompanyResearch                         │
│  UX: live Slack message updates per tool call    │
│  Gaps: explicitly flagged, never hallucinated    │
└────────────┬─────────────────────────────────────┘
             │
             ▼
     ┌───────────────┐
     │  CHECKPOINT 1 │  Research brief posted in Slack
     │               │  Rep reviews: facilities, initiatives,
     │               │  trigger events, Exception Tax estimate
     │               │  Bot asks: "Ready to pull contacts?"
     └───────┬───────┘
             │
             ▼
┌──────────────────────────────────────┐
│  Agent 3: Contact Sourcer            │
│  Tools: Apollo (people search)       │
│  Filters: persona type, seniority,   │
│           department, location       │
│  Output: Persona[] (max 10)          │
└────────────┬─────────────────────────┘
             │
             ▼
     ┌───────────────┐
     │  CHECKPOINT 2 │  Contact list posted in Slack
     │               │  Rep removes contacts, flags up to 3
     │               │  for deep individual research
     │               │  Bot enforces 3-contact cap
     └───────┬───────┘
             │
             ├─────────────────────────────────────────┐
             │                                         │
             ▼ (flagged contacts only, runs parallel)  │
┌───────────────────────────────────┐                  │
│  Agent 4: Individual Researcher   │                  │
│  Tools: search_web (Exa),         │                  │
│         fetch_url (LinkedIn)      │                  │
│  Per contact: tenure, prior roles,│                  │
│  recent posts, speaking activity  │                  │
│  Output: ContactResearch[]        │                  │
│  Gaps: explicitly flagged         │                  │
└────────────┬──────────────────────┘                  │
             │                                         │
             └─────────────────────────────────────────┘
             │ (merge: all contacts + research where available)
             ▼
┌──────────────────────────────────────────────────┐
│  Agent 5: Scorer & Value Mapper                  │
│  Tools: Google Drive (account plan),             │
│         Gong (call transcripts),                 │
│         HubSpot (contact/account history)        │
│  Inputs: CompanyResearch + ContactResearch[]     │
│  Output: ScoredPersona[] sorted by score         │
│  All 10 contacts scored                          │
│  Flagged 3: deep personalization tier            │
│  Remaining 7: standard personalization tier      │
└────────────┬─────────────────────────────────────┘
             │
             ▼  (runs in parallel per approved persona)
┌──────────────────────────────────────────────────┐
│  Agent 6: Sequence Generator                     │
│  Inputs per persona:                             │
│    Deep tier: company research + individual      │
│               research + value driver            │
│    Standard tier: company research + persona     │
│                   type + value driver            │
│  Exception Tax woven into FS/ODM sequences       │
│  Output: Sequence (draft) per persona            │
└────────────┬─────────────────────────────────────┘
             │
             ▼
     ┌───────────────┐
     │  CHECKPOINT 3 │  Rep reviews each step in Slack thread
     │  Edit loop    │  Free-form natural language edits
     │               │  Bot regenerates, rep re-reviews
     │               │  Repeats until rep types "approve"
     └───────┬───────┘
             │
             ▼
┌──────────────────────────────────┐
│  Agent 7: Sequence Brief Delivery│
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

## Company Researcher: Document Retrieval Logic

### Source Priority Order
1. **SEC EDGAR** (public companies) — 10-K and 10-Q filings via EDGAR full-text search API. Prefer HTML filing over PDF when available.
2. **Company IR page** — Earnings call transcripts, investor presentations, press releases. Located via Exa search.
3. **Exa web search** — News, press releases, trade publications, LinkedIn posts from executives.
4. **PDF fallback** — When document is only available as PDF: download, extract text with `pdfplumber`, target sections by header keywords.

### 10-K Targeted Sections (Do Not Read Full Document)
| Section | What to Extract |
|---|---|
| Item 1 — Business | How the company describes its operations, distribution network, technology |
| Item 1A — Risk Factors | Stated risks related to inventory, supply chain, technology, labor |
| Item 7 — MD&A | Management's stated priorities, challenges, capital investments |
| Capex mentions anywhere | Technology investment, automation, facility expansion |

### Private Company Fallback
When EDGAR is not available (private company or thin public filing):
- Press releases via Exa
- News coverage: expansions, acquisitions, leadership hires
- Executive LinkedIn posts (public)
- Industry publications and case studies

### Research Gap Protocol
When a specific data point cannot be found, the bot surfaces it explicitly in the brief:
```
*Board Initiatives*
→ Supply chain resilience investment — per Q3 2025 earnings call
→ DC network expansion in Southeast US — per November 2025 press release
→ Could not find a third sourced initiative. Worth checking IR page or recent Gong calls.

*Automation Vendors*
→ None identified from public sources. Worth asking on discovery call.
```
No hallucination. No silent omissions. Gaps become talking points.

---

## Exception Tax Formula

Calculated deterministically. Not LLM-generated. Shown in research brief and woven into FS/ODM sequences.

**Step 1 — Pallet positions:**
```
positions = totalSqFt × 0.60 × 4 / 36
```

**Step 2 — Annual savings (conservative mid-range, 2.5% error rate):**
```
savings = positions × 0.025 × 1.0 × 4 × $100 × 0.80
```

**Variables:**
- `0.60` = 60% usable floor space
- `4` = average racking levels
- `36` = sq ft per pallet position
- `0.025` = 2.5% error rate (97.5% accuracy)
- `1.0` = 100% of positions scanned per cycle
- `4` = quarterly cycles per year
- `$100` = cost per exception event
- `0.80` = 80% reduction in exceptions with Gather

Always show full math in the brief so reps can sanity-check and cite it.

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
- Company research surfaces a trigger event directly relevant to this persona's domain → elevate one tier

### Lane Routing
```
outreach_lane = "AE"   if seniority in ["C-Suite", "SVP", "VP"]
outreach_lane = "MDR"  if seniority in ["Director", "Manager"]
```

### Persona Title Keywords (for Apollo search)

| Type | Title Keywords |
|---|---|
| TDM | Head of CI, Director of CI, Industrial Engineer, Automation Manager, Director of Engineering & Automation, VP Engineering, Director of Continuous Improvement |
| ODM | Director of Operations, VP Warehouse & Distribution, Director of Inventory & Supply Chain, Director of Warehouse Operations, Inventory Manager, VP Operations, VP Supply Chain |
| FS | CSCO, COO, EVP Operations, SVP Operations, EVP Supply Chain, SVP Supply Chain, Head of Network Operations, President |
| IT | VP Information Technology, Manager IT Systems, IT Business Systems Integration Manager, Director Enterprise Systems |
| Safety | VP Risk Management, EHS Manager, Director of Safety, VP Risk |

**Max contacts returned from Apollo:** 10 per account, sorted High → Medium → Low
**Deep research cap:** 3 contacts per session (rep-flagged)

---

## Value Driver → Persona Mapping

| Persona | Primary Value Driver | Hook Source |
|---|---|---|
| TDM | `cycle_count_accuracy`, `wms_integration_stability`, `labor_reduction` | Gong themes + account plan + company research |
| ODM | `shrink_reduction`, `throughput_preservation`, `labor_reduction` | Gong themes + trigger events from company research |
| FS | `network_roi`, `competitive_differentiation` | Exception Tax estimate + account plan goals + board initiatives |
| IT | `wms_integration_stability`, `compliance_traceability` | None (use standard SOC 2 / integration messaging) |
| Safety | `compliance_traceability` | None (use standard OSHA / safety messaging) |

---

## Slack Interaction Definitions

### Phase 1 — Trigger & Normalization
- Rep sends free-form message in designated channel
- Bot replies with parsed intent card:
  > *"Got it — researching **Acme Corp**. 3PL compliance angle. Is that right?"*
- Rep: ✅ **Yes, run it** / ✏️ **Edit**
- If confidence < 0.7, bot asks a single clarification question before proceeding

### Phase 2 — Company Research (Live Progress)
- Bot posts a single Slack message and updates it in place as each research step completes:
  ```
  Researching Acme Corp...
  ✓ Company overview + facility count (23 DCs identified)
  ✓ 10-K filed — reading MD&A and Risk Factors...
  ✓ Found trigger event: $31M inventory write-down cited in FY2024 10-K
  ✓ Found 2 press releases (DC expansion, new WMS deployment)
  ⏳ Searching for earnings call transcript...
  ```
- Final update replaces progress with the full research brief

### Checkpoint 1 — Research Review
- Bot posts the `CompanyResearch` brief as structured Slack blocks:
  - Facility count + sq footage estimate
  - Board initiatives (sourced)
  - Company priorities
  - Trigger events
  - Automation vendors (or "None identified")
  - Exception Tax estimate with math shown
  - Research gaps flagged explicitly
- Bot asks: *"Ready to pull contacts?"*
- Rep: ✅ **Yes, find contacts** / 💬 **I have a question**

### Phase 3 — Contact Sourcing
- Bot pulls from Apollo, posts contact list as Slack blocks
- Each contact shows: Name, Title, Persona type badge, Seniority, Lane (AE/MDR)

### Checkpoint 2 — Contact Review + Deep Research Flagging
- Rep can remove contacts (button per row)
- Rep can flag up to 3 contacts for deep individual research (⭐ button)
- Bot enforces the 3-contact cap:
  > *"You've flagged 3 contacts for deep research — that's the max. Remove one to add another."*
- Rep hits ✅ **Confirm & Run Research**

### Phase 4 — Individual Research (Flagged Contacts Only)
- Bot posts brief progress update per flagged contact (same live-update pattern)
- Runs in parallel for all flagged contacts

### Phase 5 — Sequence Generation
- Runs automatically after individual research completes
- Bot posts: *"Generating sequences for all 10 contacts..."*

### Checkpoint 3 — Sequence Review & Edit Loop
- Bot posts sequence steps per persona as threaded Slack messages
- Each step shows: Step #, Channel, Day offset, Subject line (if email), Body copy
- Sequences for deep-researched contacts labeled: *⭐ Deep personalization*
- Rep replies in thread with natural language:
  - *"Change the subject line"*
  - *"Make step 2 shorter"*
  - *"Regenerate the whole thing with a compliance angle"*
- Bot applies edit, posts updated step
- Rep types **"approve"** or clicks ✅ to finalize each step

### Phase 6 — Sequence Brief Delivery
- Bot posts final Sequence Brief per persona as formatted Slack blocks
- Each step is copy-paste ready (subject + body clearly separated)
- Bot message:
  > *"Here's your sequence for [Name] at [Company]. Ready to paste into Apollo."*

---

## Integration Contracts

| Integration | Agent | Usage | Direction |
|---|---|---|---|
| Slack | All agents | Receive rep messages, post + update responses, interactive blocks | Bidirectional |
| SEC EDGAR | Company Researcher | Fetch 10-K and 10-Q filings by company ticker | Read |
| Exa | Company Researcher, Individual Researcher | Web search, fetch press releases, earnings transcripts, news, LinkedIn posts | Read |
| HubSpot | Input Normalizer, Scorer | Account lookup, contact history, activity update | Read + Write |
| Apollo | Contact Sourcer | Find people by company + title keywords, return contact data | Read |
| Google Drive | Scorer | Fetch account plan for target account | Read |
| Gong | Scorer | Fetch call transcripts for account | Read |
| Apollo | Delivery | No API — copy-paste workflow (v1) | None (v1) |

---

## Open Items (Deferred)

1. **Sequence templates** — Step count, channel mix (email/LinkedIn/call), and day offsets for AE lane vs. MDR lane. Defines the Sequence Generator's execution contract.
2. **Apollo upgrade path** — When to swap copy-paste for a real integration (Outreach/Salesloft as alternatives worth evaluating).
3. **LinkedIn individual research** — Direct scraping may require a third-party LinkedIn API proxy. Evaluate Exa's LinkedIn coverage vs. dedicated scraper before building Agent 4.
4. **Earnings transcript paywalls** — Seeking Alpha free tier has rate limits. Monitor hit rate; if inadequate, evaluate a dedicated financial data API.
5. **Exception Tax sqft estimation** — For companies with no public sqft data, the formula needs a defensible per-facility average by industry/company type. Define the lookup table before Agent 2 ships.
