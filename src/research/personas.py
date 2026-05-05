"""The four ICP personas the Account Research Bot scopes to.

Aligned to the Gather AI Knowledge Base buyer-persona framework:
  1. Technical Lead — primary entry point in new logo deals (CI / Automation /
     Industrial Engineering directors). Most likely to book a first meeting.
  2. Operations Lead — owns adoption (VP Ops, GM Warehouse, Inventory Control).
     Second-best meeting booker.
  3. Executive — CSCO / COO / SVP Ops. Engaged late; hard to cold-book but the
     #1 closed-lost gap when missed.
  4. Compliance Lead — IT + Safety/EHS gatekeepers. Surface late = deal risk.

Title keyword lists are kept tight (≤6 each) — Apollo's `person_titles`
filter degrades to 422 Unprocessable Entity when the array gets long,
and tighter filters return higher-fit contacts anyway. Keys are stable
identifiers used in Slack action values and session state.
"""
from typing import Dict, Iterable, List, TypedDict


class PersonaConfig(TypedDict):
    label: str
    title_keywords: List[str]
    # Word-boundary terms that disqualify a contact even if Apollo
    # returned them. Drives `title_filter.filter_by_persona_fit`. Each
    # term is matched case-insensitively with `\b…\b` so "IT" hits
    # "VP IT Operations" but not "Bit" or "Fitness".
    negative_keywords: List[str]


PERSONAS: Dict[str, PersonaConfig] = {
    "technical_lead": {
        "label": "Technical Lead — CI / Automation / Engineering",
        "title_keywords": [
            "Director of Continuous Improvement",
            "Director of Industrial Engineering",
            "Director of Automation",
            "VP Engineering",
            "Industrial Engineer",
            "Continuous Improvement Manager",
        ],
        # CI/IE/Automation directors. Exclude software/IT engineering
        # ladders and revenue-side roles ("Sales Engineering" is a real
        # title that Apollo will surface for "VP Engineering").
        "negative_keywords": [
            "IT", "Information Technology", "Software", "DevOps",
            "Sales Engineering", "Customer", "Product", "Marketing",
            "Finance", "People",
        ],
    },
    "operations_lead": {
        "label": "Operations Lead — Warehouse / DC / Inventory Ops",
        "title_keywords": [
            "VP Operations",
            "Director of Operations",
            "Director of Warehouse",
            "Director of Distribution",
            "Director of Inventory Control",
            "Director of Fulfillment",
        ],
        # The biggest noise source on this persona — "VP Operations"
        # over-pulls Sales Ops, IT Ops, RevOps, People Ops, etc.
        "negative_keywords": [
            "IT", "Information Technology", "Sales", "Finance",
            "Marketing", "HR", "Human Resources", "People", "Revenue",
            "Customer Success", "Customer Service", "Legal", "Product",
        ],
    },
    "executive": {
        "label": "Executive — CSCO / COO / SVP Ops",
        "title_keywords": [
            "Chief Supply Chain Officer",
            "Chief Operating Officer",
            "SVP Operations",
            "SVP Supply Chain",
            "EVP Operations",
        ],
        # SVP/EVP Operations is the trap — Apollo returns SVP Sales Ops,
        # SVP People, SVP Marketing Ops on this filter.
        "negative_keywords": [
            "IT", "Information Technology", "Sales", "Marketing",
            "Finance", "HR", "Human Resources", "People", "Legal",
            "Product", "Revenue", "Customer",
        ],
    },
    "compliance_lead": {
        "label": "Compliance Lead — IT / Safety / EHS",
        "title_keywords": [
            "Director of IT",
            "VP Information Technology",
            "Director of EHS",
            "VP Safety",
            "Director of Loss Prevention",
        ],
        # IT/Safety/EHS IS the persona — DO NOT exclude IT here.
        # Filter out the unrelated "Director of …" matches.
        "negative_keywords": [
            "Sales", "Marketing", "Finance", "HR", "Human Resources",
            "Customer Success", "Product", "Revenue",
        ],
    },
}


def map_personas_to_title_keywords(persona_keys: Iterable[str]) -> List[str]:
    keywords: List[str] = []
    seen: set = set()
    for key in persona_keys:
        cfg = PERSONAS.get(key)
        if cfg is None:
            continue
        for kw in cfg["title_keywords"]:
            if kw not in seen:
                seen.add(kw)
                keywords.append(kw)
    return keywords
