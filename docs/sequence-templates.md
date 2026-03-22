# Sequence Templates — Gather AI Prospecting Bot

These templates define the structure and copy framework for the Sequence Generator agent. All variable tokens are filled by the agent at generation time. Rep reviews and edits before anything is delivered.

---

## AE Lane (VP+: COO, CSCO, SVP/EVP Ops, SVP/EVP Supply Chain)

**Philosophy:** Short, strategic, network-level framing. One point per touch. CTA is always a 15-minute conversation, never a demo.

| Step | Channel | Day | Subject | Purpose |
|---|---|---|---|---|
| 1 | Email | 0 | `{{first_name}}, [Gong hook or account signal]` | Lead with what you know about them specifically |
| 2 | LinkedIn | 3 | — | Connect request, no note or single-line note |
| 3 | Email | 7 | `Re: [Step 1 subject]` | Follow up, add network ROI proof point |
| 4 | Email | 14 | `[Company] + Gather AI` | Business case email — network scale framing |
| 5 | Email | 21 | `Still worth a conversation?` | Breakup email — low pressure, leaves door open |

### Step 1
```
{{first_name}},

{{gong_hook_or_signal}}

We work with {{comparable_customer}} to {{specific_outcome}}. Given
{{company}}'s footprint, I think there's a similar story here.

Worth 15 minutes to find out?

{{rep_name}}
```

### Step 2 (LinkedIn)
```
{{first_name}} — reaching out about inventory operations at {{company}}.
{{rep_name}} @ Gather AI
```

### Step 3
```
{{first_name}},

Following up on my note from last week.

One thing that comes up consistently with {{vertical}} operators at your
scale: inventory inaccuracy isn't a facility problem, it's a network problem.
The variance across DCs makes benchmarking and capital allocation harder
than it needs to be.

{{comparable_customer}} went from 87% to 99%+ location accuracy across their
network in under 6 months. Happy to share how they got there.

15 minutes this week?

{{rep_name}}
```

### Step 4
```
{{first_name}},

Quick math for {{company}}:

If {{dc_count}} facilities each recover meaningful labor hours per week from
automated cycle counting, that's significant — before you factor in shrink
reduction and pick error rate improvement.

We can walk through what that looks like for your specific operation.
No slides, just numbers.

{{rep_name}}
```

### Step 5
```
{{first_name}},

I'll keep this short — is inventory accuracy a priority for {{company}}
this year or not the right time?

Either answer is useful. Happy to reconnect when timing is better.

{{rep_name}}
```

---

## MDR Lane (Director/Manager: Director of CI, Director of Ops, Inventory Manager, Automation Manager)

**Philosophy:** More touches, more channels, operational pain framing. These people live the problem daily. LinkedIn is effective. CTA is a discovery call or intro to the AE.

| Step | Channel | Day | Subject | Purpose |
|---|---|---|---|---|
| 1 | Email | 0 | `{{first_name}}, cycle counting at {{company}}` | Lead with their specific operational pain |
| 2 | LinkedIn | 2 | — | Connect request with short context note |
| 3 | Email | 5 | `Re: [Step 1 subject]` | Follow up, add role-specific proof point |
| 4 | LinkedIn | 9 | — | Short message if connected |
| 5 | Email | 14 | `How {{comparable_customer}} fixed this` | Customer story relevant to their vertical |
| 6 | Email | 19 | `Quick question about {{company}}'s cycle count process` | Curiosity/discovery angle |
| 7 | Email | 25 | `Closing the loop` | Breakup email |

### Step 1
```
{{first_name}},

{{gong_hook_or_value_driver_opener}}

We help {{vertical}} operators like {{company}} get to 99%+ inventory
location accuracy — 15x faster than manual counting — without disrupting
operations or replacing your WMS.

Worth a quick call to see if it fits what you're working on?

{{rep_name}}
Gather AI
```

### Step 2 (LinkedIn)
```
{{first_name}} — sent you a note about inventory accuracy at {{company}}.
Thought it was worth connecting directly. {{rep_name}} @ Gather AI
```

### Step 3
```
{{first_name}},

Following up from last week.

The teams we work with typically spend significant time on manual cycle
counts and still finish the quarter with location accuracy below 90%.
The problem isn't effort, it's the tool.

Gather AI drops a drone-based autonomous scanning layer on top of your
existing WMS. No data science team, no rip-and-replace.
{{comparable_customer}} cut manual count labor by 65% in the first 90 days.

Open to a 20-minute call this week?

{{rep_name}}
```

### Step 4 (LinkedIn — if connected)
```
{{first_name}} — did my email land? Wanted to make sure it didn't get
buried. Happy to share what we've done with {{comparable_customer}} if
it's relevant to what you're working on at {{company}}.
```

### Step 5
```
{{first_name}},

{{comparable_customer}} was dealing with the same challenge as {{company}} —
{{specific_pain}}.

They went live with Gather AI in 6 weeks. {{specific_outcome}}.
Happy to make an intro to their ops team if helpful.

{{rep_name}}
```

### Step 6
```
{{first_name}},

One quick question: what does your current cycle count process actually
look like at {{company}}? RF guns, dedicated team, how often?

Not a pitch — genuinely trying to understand if what we do is relevant
before I keep following up.

{{rep_name}}
```

### Step 7
```
{{first_name}},

Closing the loop on my end. If inventory accuracy isn't a current priority
at {{company}}, no problem — I'll check back in next quarter.

If it is and the timing just hasn't been right, I'm easy to reach.

{{rep_name}}
```

---

## Token Reference

| Token | Source | Fallback |
|---|---|---|
| `{{first_name}}` | Clay persona | Required — no fallback |
| `{{company}}` | NormalizedRequest | Required — no fallback |
| `{{rep_name}}` | Slack user profile | Required — no fallback |
| `{{dc_count}}` | HubSpot or Google Drive account plan | Omit line if unavailable |
| `{{vertical}}` | ICP classification | "logistics" |
| `{{gong_hook_or_signal}}` | Gong transcript theme or LinkedIn signal | Default value driver opener |
| `{{gong_hook_or_value_driver_opener}}` | Gong transcript theme or value driver | Default pain point for persona type |
| `{{comparable_customer}}` | Customer reference matched by vertical | "one of our customers" |
| `{{specific_outcome}}` | Value driver proof point | Standard proof point for value driver |
| `{{specific_pain}}` | Account plan or Gong theme | Standard pain point for vertical |

---

## Notes

- Templates are reviewed and edited by the rep before delivery — treat them as a strong first draft, not final copy
- Gong hooks take priority over generic openers whenever available
- Account plan context takes priority over vertical defaults
- Comparable customer references should be matched by vertical (3PL for 3PL, F&B for F&B, etc.)
- Rep feedback on what's working should be used to update these templates over time
