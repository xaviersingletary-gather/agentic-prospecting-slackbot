"""The four ICP personas the Account Research Bot scopes to.

Aligned to the Gather AI Knowledge Base buyer-persona framework:
  1. Technical Lead — primary entry point in new logo deals (CI / Automation /
     Industrial Engineering directors). Most likely to book a first meeting.
  2. Operations Lead — owns adoption (VP Ops, GM Warehouse, Inventory Control).
     Second-best meeting booker.
  3. Executive — CSCO / COO / SVP Ops. Engaged late; hard to cold-book but the
     #1 closed-lost gap when missed.
  4. Compliance Lead — IT + Safety/EHS gatekeepers. Surface late = deal risk.

Keys are stable identifiers used in Slack action values and session state.
"""
from typing import Dict, Iterable, List, TypedDict


class PersonaConfig(TypedDict):
    label: str
    title_keywords: List[str]


PERSONAS: Dict[str, PersonaConfig] = {
    "technical_lead": {
        "label": "Technical Lead — CI / Automation / Engineering",
        "title_keywords": [
            "Continuous Improvement",
            "Director of Continuous Improvement",
            "Director of CI",
            "Industrial Engineer",
            "Industrial Engineering",
            "Automation Engineering",
            "Automation Manager",
            "Director of Automation",
            "VP Engineering",
            "VP of Engineering",
            "VP of Logistics Technology",
            "Manufacturing Technology",
            "Process Improvement",
            "Innovation Engineer",
            "Head of Engineering",
            "Director of Industrial Engineering",
            "Director of Process Improvement",
        ],
    },
    "operations_lead": {
        "label": "Operations Lead — Warehouse / DC / Inventory Ops",
        "title_keywords": [
            "VP Operations",
            "VP of Operations",
            "Director of Operations",
            "VP Warehouse",
            "VP of Warehouse",
            "Director of Warehouse",
            "Director of Distribution",
            "GM Warehouse",
            "Head of Warehouse",
            "Director of Inventory",
            "Director of Supply Chain",
            "Director of ICQA",
            "Inventory Control Manager",
            "ICQA Manager",
            "Sr. Manager DC Operations",
            "VP Fulfillment",
            "VP of Fulfillment",
            "Director of Fulfillment",
        ],
    },
    "executive": {
        "label": "Executive — CSCO / COO / SVP Ops",
        "title_keywords": [
            "Chief Supply Chain",
            "CSCO",
            "Chief Operating Officer",
            "COO",
            "EVP Operations",
            "SVP Operations",
            "EVP Supply Chain",
            "SVP Supply Chain",
            "Head of Network Operations",
        ],
    },
    "compliance_lead": {
        "label": "Compliance Lead — IT / Safety / EHS",
        "title_keywords": [
            "Director of Infrastructure",
            "VP of Information Technology",
            "VP IT",
            "Director of IT",
            "Director of Enterprise Architecture",
            "WMS Systems",
            "Director of EHS",
            "VP Risk Management",
            "VP Health Safety",
            "Director of Operations Risk",
            "Corporate Safety Director",
            "Director of Loss Prevention",
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
