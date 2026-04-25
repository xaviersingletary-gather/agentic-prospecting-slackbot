import copy
import logging
import uuid
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Template definitions — tokens use {{token_name}} syntax
# ---------------------------------------------------------------------------

AE_STEPS = [
    {
        "step_number": 1,
        "channel": "email",
        "day_offset": 0,
        "subject_line": "{{first_name}}, {{gong_hook_subject}}",
        "body": (
            "{{first_name}},\n\n"
            "{{gong_hook_or_signal}}\n\n"
            "We work with {{comparable_customer}} to {{specific_outcome}}. Given "
            "{{company}}'s footprint, I think there's a similar story here.\n\n"
            "Worth 15 minutes to find out?\n\n"
            "{{rep_name}}"
        ),
    },
    {
        "step_number": 2,
        "channel": "linkedin",
        "day_offset": 3,
        "subject_line": None,
        "body": (
            "{{first_name}} — reaching out about inventory operations at {{company}}.\n"
            "{{rep_name}} @ Gather AI"
        ),
    },
    {
        "step_number": 3,
        "channel": "email",
        "day_offset": 7,
        "subject_line": "Re: {{first_name}}, {{gong_hook_subject}}",
        "body": (
            "{{first_name}},\n\n"
            "Following up on my note from last week.\n\n"
            "One thing that comes up consistently with {{vertical}} operators at your scale: "
            "inventory inaccuracy isn't a facility problem, it's a network problem. "
            "The variance across DCs makes benchmarking and capital allocation harder than it needs to be.\n\n"
            "{{comparable_customer}} went from 87% to 99%+ location accuracy across their "
            "network in under 6 months. Happy to share how they got there.\n\n"
            "15 minutes this week?\n\n"
            "{{rep_name}}"
        ),
    },
    {
        "step_number": 4,
        "channel": "email",
        "day_offset": 14,
        "subject_line": "{{company}} + Gather AI",
        "body": (
            "{{first_name}},\n\n"
            "Quick math for {{company}}:\n\n"
            "If {{dc_count}} facilities each recover meaningful labor hours per week from "
            "automated cycle counting, that's significant — before you factor in shrink "
            "reduction and pick error rate improvement.\n\n"
            "We can walk through what that looks like for your specific operation. "
            "No slides, just numbers.\n\n"
            "{{rep_name}}"
        ),
    },
    {
        "step_number": 5,
        "channel": "email",
        "day_offset": 21,
        "subject_line": "Still worth a conversation?",
        "body": (
            "{{first_name}},\n\n"
            "I'll keep this short — is inventory accuracy a priority for {{company}} "
            "this year or not the right time?\n\n"
            "Either answer is useful. Happy to reconnect when timing is better.\n\n"
            "{{rep_name}}"
        ),
    },
]

MDR_STEPS = [
    {
        "step_number": 1,
        "channel": "email",
        "day_offset": 0,
        "subject_line": "{{first_name}}, cycle counting at {{company}}",
        "body": (
            "{{first_name}},\n\n"
            "{{gong_hook_or_value_driver_opener}}\n\n"
            "We help {{vertical}} operators like {{company}} get to 99%+ inventory "
            "location accuracy — 15x faster than manual counting — without disrupting "
            "operations or replacing your WMS.\n\n"
            "Worth a quick call to see if it fits what you're working on?\n\n"
            "{{rep_name}}\n"
            "Gather AI"
        ),
    },
    {
        "step_number": 2,
        "channel": "linkedin",
        "day_offset": 2,
        "subject_line": None,
        "body": (
            "{{first_name}} — sent you a note about inventory accuracy at {{company}}. "
            "Thought it was worth connecting directly. {{rep_name}} @ Gather AI"
        ),
    },
    {
        "step_number": 3,
        "channel": "email",
        "day_offset": 5,
        "subject_line": "Re: {{first_name}}, cycle counting at {{company}}",
        "body": (
            "{{first_name}},\n\n"
            "Following up from last week.\n\n"
            "The teams we work with typically spend significant time on manual cycle "
            "counts and still finish the quarter with location accuracy below 90%. "
            "The problem isn't effort, it's the tool.\n\n"
            "Gather AI drops a drone-based autonomous scanning layer on top of your "
            "existing WMS. No data science team, no rip-and-replace. "
            "{{comparable_customer}} cut manual count labor by 65% in the first 90 days.\n\n"
            "Open to a 20-minute call this week?\n\n"
            "{{rep_name}}"
        ),
    },
    {
        "step_number": 4,
        "channel": "linkedin",
        "day_offset": 9,
        "subject_line": None,
        "body": (
            "{{first_name}} — did my email land? Wanted to make sure it didn't get buried. "
            "Happy to share what we've done with {{comparable_customer}} if it's relevant "
            "to what you're working on at {{company}}."
        ),
    },
    {
        "step_number": 5,
        "channel": "email",
        "day_offset": 14,
        "subject_line": "How {{comparable_customer}} fixed this",
        "body": (
            "{{first_name}},\n\n"
            "{{comparable_customer}} was dealing with the same challenge as {{company}} — "
            "{{specific_pain}}.\n\n"
            "They went live with Gather AI in 6 weeks. {{specific_outcome}}. "
            "Happy to make an intro to their ops team if helpful.\n\n"
            "{{rep_name}}"
        ),
    },
    {
        "step_number": 6,
        "channel": "email",
        "day_offset": 19,
        "subject_line": "Quick question about {{company}}'s cycle count process",
        "body": (
            "{{first_name}},\n\n"
            "One quick question: what does your current cycle count process actually "
            "look like at {{company}}? RF guns, dedicated team, how often?\n\n"
            "Not a pitch — genuinely trying to understand if what we do is relevant "
            "before I keep following up.\n\n"
            "{{rep_name}}"
        ),
    },
    {
        "step_number": 7,
        "channel": "email",
        "day_offset": 25,
        "subject_line": "Closing the loop",
        "body": (
            "{{first_name}},\n\n"
            "Closing the loop on my end. If inventory accuracy isn't a current priority "
            "at {{company}}, no problem — I'll check back in next quarter.\n\n"
            "If it is and the timing just hasn't been right, I'm easy to reach.\n\n"
            "{{rep_name}}"
        ),
    },
]


# ---------------------------------------------------------------------------
# Token resolution helpers
# ---------------------------------------------------------------------------

def _resolve_vertical(account_description: Optional[str]) -> str:
    if not account_description:
        return "logistics"
    desc_lower = account_description.lower()
    vertical_map = {
        "manufacturing": "manufacturing",
        "food": "food and beverage",
        "beverage": "food and beverage",
        "pharmaceutical": "healthcare/pharma",
        "pharma": "healthcare/pharma",
        "healthcare": "healthcare",
        "retail": "retail",
        "ecommerce": "e-commerce",
        "3pl": "3PL",
        "logistics": "logistics",
        "automotive": "automotive",
        "apparel": "apparel",
    }
    for keyword, label in vertical_map.items():
        if keyword in desc_lower:
            return label
    return "logistics"


def _resolve_hook(persona: dict, company_research: Optional[dict]) -> tuple:
    """
    Returns (hook_body, hook_subject).
    Priority: gong_hook > company research trigger event > Exa/Drive research hook
    > LinkedIn signal > value driver default opener.
    """
    # 1. Gong hook (call transcript)
    gong_hook = persona.get("gong_hook")
    if gong_hook:
        return gong_hook, "a recent conversation"

    # 2. Company research trigger event
    if company_research:
        trigger_events = company_research.get("trigger_events") or []
        if trigger_events:
            t = trigger_events[0]
            desc = t.get("description", "")[:200]
            if desc:
                return (
                    f"Noticed {desc.lower().rstrip('.')} — thought it was worth reaching out.",
                    "a recent development",
                )
        # 3. Board initiative as hook
        board_initiatives = company_research.get("board_initiatives") or []
        if board_initiatives:
            init = board_initiatives[0]
            title = init.get("title", "")
            source = init.get("source", "recent reports")
            if title:
                return (
                    f"Saw that {company_research.get('account_name', 'your team')} "
                    f"is focused on {title.lower()} — per {source}.",
                    "a strategic priority",
                )

    # 4. Exa/Drive research hook
    value_driver = persona.get("value_driver") or {}
    research_hook = value_driver.get("research_hook")
    if research_hook:
        return research_hook, "recent activity"

    # 5. LinkedIn signal
    signals = persona.get("linkedin_signals") or []
    if signals:
        top = signals[0]
        content = top.get("content", "")[:120]
        if content:
            return (
                f"Saw your recent activity around {content.lower().rstrip('.')} — "
                "thought it was relevant context for this note.",
                "your recent post",
            )

    # 6. Value driver default opener
    opener = value_driver.get("default_opener", "")
    return opener, "inventory operations"


def _resolve_individual_opener(contact_research: Optional[dict]) -> str:
    """
    Build a personalized opening sentence from individual contact research.
    Used in deep-tier sequences to reference the contact's own background.
    """
    if not contact_research:
        return ""

    # Recent LinkedIn post — most timely signal
    linkedin = contact_research.get("recent_linkedin") or []
    if linkedin:
        content = linkedin[0].get("content", "")[:120]
        if content:
            return f"Came across your recent post about {content.lower().rstrip('.')}."

    # Speaking activity
    speaking = contact_research.get("speaking_activity")
    if speaking:
        return f"Came across {speaking[:100].lower().rstrip('.')} — great context."

    # Prior company reference
    prior_roles = contact_research.get("prior_roles") or []
    if prior_roles:
        prior_company = prior_roles[0].get("company", "")
        if prior_company:
            return f"Knowing your background at {prior_company}, I thought this would be directly relevant."

    return ""


def _fill_tokens(text: Optional[str], tokens: dict) -> Optional[str]:
    if text is None:
        return None
    for key, value in tokens.items():
        text = text.replace(f"{{{{{key}}}}}", str(value) if value else "")
    return text


# ---------------------------------------------------------------------------
# Generator agent
# ---------------------------------------------------------------------------

class SequenceGeneratorAgent:
    def generate(
        self,
        persona: dict,
        account_name: str,
        account_description: Optional[str] = None,
        rep_name: str = "your rep",
        session_id: str = "",
        company_research: Optional[dict] = None,
        contact_research: Optional[dict] = None,
        theme_assignment: Optional[dict] = None,
    ) -> dict:
        """
        Generate a full sequence for a single scored persona.
        Returns a sequence dict with id, persona_id, lane, and steps.
        """
        lane = persona.get("outreach_lane", "MDR")
        value_driver = persona.get("value_driver") or {}
        vertical = _resolve_vertical(account_description)

        # Resolve the opening hook — prefer company research trigger events over gong/LinkedIn
        hook_body, hook_subject = _resolve_hook(persona, company_research)

        # Prepend theme opener if available — theme leads, research hook follows as supporting context
        if theme_assignment and theme_assignment.get("opener"):
            hook_body = (
                f"{theme_assignment['opener']}\n\n{hook_body}"
                if hook_body
                else theme_assignment["opener"]
            )

        # DC count — prefer company research over placeholder
        dc_count = "your"
        if company_research and company_research.get("facility_count"):
            dc_count = str(company_research["facility_count"])

        # Individual personalization opener (deep tier only)
        individual_opener = _resolve_individual_opener(contact_research) if contact_research else ""

        tokens = {
            "first_name": persona.get("first_name", ""),
            "company": account_name,
            "rep_name": rep_name,
            "vertical": vertical,
            "comparable_customer": value_driver.get("comparable_customer", "one of our customers"),
            "specific_outcome": value_driver.get("outcome", "significant improvements in inventory accuracy"),
            "specific_pain": value_driver.get("pain_point", "inventory accuracy challenges"),
            "gong_hook_or_signal": hook_body,
            "gong_hook_or_value_driver_opener": hook_body,
            "gong_hook_subject": hook_subject,
            "dc_count": dc_count,
            "individual_opener": individual_opener,
        }

        template_steps = copy.deepcopy(AE_STEPS if lane == "AE" else MDR_STEPS)

        filled_steps = []
        for step in template_steps:
            filled_steps.append({
                "step_number": step["step_number"],
                "channel": step["channel"],
                "day_offset": step["day_offset"],
                "subject_line": _fill_tokens(step.get("subject_line"), tokens),
                "body": _fill_tokens(step.get("body"), tokens),
                "status": "draft",
            })

        personalization_tier = "deep" if contact_research else "standard"

        sequence = {
            "id": str(uuid.uuid4()),
            "session_id": session_id,
            "persona_id": persona["id"],
            "lane": lane,
            "personalization_tier": personalization_tier,
            "status": "draft",
            "steps": filled_steps,
            "edit_history": [],
            "created_at": datetime.utcnow().isoformat(),
        }

        logger.info(
            f"[generator] Generated {lane} sequence ({len(filled_steps)} steps) "
            f"for {persona.get('first_name')} {persona.get('last_name')}"
        )
        return sequence
