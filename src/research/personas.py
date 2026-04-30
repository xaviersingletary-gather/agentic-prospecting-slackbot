"""The four ICP personas the Account Research Bot scopes to (spec §1.3).

Each persona maps to a list of Apollo title keywords used to filter the
contact pull. Keys are stable identifiers used in Slack action values.
"""
from typing import Dict, Iterable, List, TypedDict


class PersonaConfig(TypedDict):
    label: str
    title_keywords: List[str]


PERSONAS: Dict[str, PersonaConfig] = {
    "csco": {
        "label": "CSCO / Chief Supply Chain Officer",
        "title_keywords": ["Chief Supply Chain", "CSCO", "SVP Supply Chain"],
    },
    "vp_warehouse_ops": {
        "label": "VP Warehouse Operations",
        "title_keywords": [
            "VP Warehouse",
            "VP Operations",
            "Head of Warehouse",
            "Director Warehouse Operations",
        ],
    },
    "vp_inventory_planning": {
        "label": "VP Inventory & Planning",
        "title_keywords": [
            "VP Inventory",
            "VP Planning",
            "VP S&OP",
            "Director Inventory",
        ],
    },
    "sop_lead": {
        "label": "S&OP Lead / Director",
        "title_keywords": [
            "S&OP",
            "Sales and Operations",
            "Demand Planning Director",
            "Supply Planning",
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
