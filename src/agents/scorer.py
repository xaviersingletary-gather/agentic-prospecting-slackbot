import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# Value drivers per persona type
VALUE_DRIVERS: dict[str, dict] = {
    "TDM": {
        "primary_driver": "cycle_count_efficiency",
        "pain_point": "manual cycle counting consuming engineering and labor hours with inconsistent accuracy",
        "outcome": "15x faster cycle counts with 99%+ location accuracy, no additional headcount",
        "cta_angle": "how teams like yours have eliminated manual counting without replacing the WMS",
        "default_opener": "If your team is spending time on manual cycle counts and still not hitting accuracy targets, the problem usually isn't effort — it's the tool.",
    },
    "ODM": {
        "primary_driver": "inventory_accuracy",
        "pain_point": "inventory inaccuracy creating pick errors, shrink, and WMS reconciliation gaps",
        "outcome": "99%+ inventory location accuracy with measurable reduction in shrink and pick errors",
        "cta_angle": "what the floor impact looks like at similar operations",
        "default_opener": "Inventory inaccuracy across a multi-DC network is one of those problems that compounds quietly until it shows up in a shrink report or a missed SLA.",
    },
    "FS": {
        "primary_driver": "labor_cost_reduction",
        "pain_point": "cycle count labor and shrink write-offs without a clear reduction path",
        "outcome": "measurable labor savings and shrink reduction with a clear network-level ROI and payback period",
        "cta_angle": "the financial model and payback period for your network size",
        "default_opener": "Labor cost and inventory shrink are two of the harder line items to move on the ops P&L — and most teams are still trying to solve them with more headcount.",
    },
    "IT": {
        "primary_driver": "integration_simplicity",
        "pain_point": "WMS integration complexity, third-party whitelisting, and security review overhead",
        "outcome": "standard SFTP or API integration with existing WMS — no rip-and-replace, no data science team required",
        "cta_angle": "the integration architecture and security posture",
        "default_opener": "Most warehouse automation evaluations stall on IT — specifically around WMS integration, data security, and the whitelisting process for new data sources.",
    },
    "Safety": {
        "primary_driver": "autonomous_equipment_compliance",
        "pain_point": "OSHA exposure from manual counting in active warehouse aisles",
        "outcome": "autonomous drone operations with full OSHA compliance protocols and incident logging built in",
        "cta_angle": "how the safety and compliance framework is structured for autonomous equipment",
        "default_opener": "Manual cycle counting in active warehouse aisles creates ongoing OSHA exposure that most safety teams are aware of but haven't found a scalable alternative to.",
    },
}

# Comparable customer references by vertical keyword
COMPARABLE_CUSTOMERS: dict[str, str] = {
    "manufacturing": "a large manufacturing operator",
    "food": "a national food and beverage distributor",
    "beverage": "a national food and beverage distributor",
    "pharmaceutical": "a healthcare distribution network",
    "pharma": "a healthcare distribution network",
    "healthcare": "a healthcare distribution network",
    "retail": "a national retail fulfillment operator",
    "ecommerce": "a national e-commerce fulfillment operator",
    "e-commerce": "a national e-commerce fulfillment operator",
    "3pl": "a top 3PL provider",
    "logistics": "a top 3PL provider",
    "automotive": "a national automotive parts distributor",
    "apparel": "a national apparel fulfillment operator",
}

TIER_RANK = {"High": 2, "Medium": 1, "Low": 0}
TIER_UP = {"Low": "Medium", "Medium": "High", "High": "High"}
SENIORITY_ORDER = {"C-Suite": 0, "SVP": 1, "VP": 2, "Director": 3, "Manager": 4, "IC": 5}


def get_comparable_customer(account_description: Optional[str]) -> str:
    if not account_description:
        return "one of our customers"
    desc_lower = account_description.lower()
    for keyword, customer in COMPARABLE_CUSTOMERS.items():
        if keyword in desc_lower:
            return customer
    return "one of our customers"


def has_recent_linkedin_signal(signals: list, days: int = 90) -> bool:
    """Returns True if any signal has a date within the last `days` days."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    for signal in signals:
        date_str = signal.get("date")
        if not date_str:
            continue
        try:
            signal_date = datetime.fromisoformat(date_str.replace("Z", "")).replace(tzinfo=None)
            if signal_date >= cutoff:
                return True
        except (ValueError, TypeError):
            continue
    return False


def score_persona(persona: dict, account_description: Optional[str] = None) -> dict:
    """Apply scoring and value-driver mapping to a single persona. Returns updated dict."""
    persona_type = persona.get("persona_type", "ODM")
    seniority = persona.get("seniority", "Manager")
    current_tier = persona.get("priority_score", "Medium")
    linkedin_signals = persona.get("linkedin_signals") or []

    value_driver = dict(VALUE_DRIVERS.get(persona_type, VALUE_DRIVERS["ODM"]))
    value_driver["comparable_customer"] = get_comparable_customer(account_description)

    reasoning_parts = [f"Default tier for {persona_type}: {current_tier}."]

    # C-Suite always High
    if seniority == "C-Suite":
        current_tier = "High"
        reasoning_parts.append("C-Suite lock: set to High.")
    elif has_recent_linkedin_signal(linkedin_signals):
        elevated = TIER_UP[current_tier]
        if elevated != current_tier:
            reasoning_parts.append(
                f"Recent LinkedIn activity (within 90 days): elevated from {current_tier} to {elevated}."
            )
            current_tier = elevated
        else:
            reasoning_parts.append("Recent LinkedIn activity detected (already at High).")

    persona["priority_score"] = current_tier
    persona["value_driver"] = value_driver
    persona["score_reasoning"] = " ".join(reasoning_parts)
    return persona


class ScorerAgent:
    def score(
        self,
        personas: list[dict],
        account_description: Optional[str] = None,
    ) -> list[dict]:
        """Score and value-map approved personas. Returns sorted list High -> Medium -> Low."""
        scored = [score_persona(p, account_description) for p in personas]

        scored.sort(key=lambda p: (
            -TIER_RANK.get(p["priority_score"], 1),
            SENIORITY_ORDER.get(p["seniority"], 5),
        ))

        logger.info(f"[scorer] Scored {len(scored)} personas")
        return scored
