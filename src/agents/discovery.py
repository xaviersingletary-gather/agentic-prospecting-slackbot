import logging
import uuid
from datetime import datetime
from typing import Optional

from src.config import settings  # noqa: F401 (used for MOCK_PERSONAS)
from src.integrations.apollo import ApolloClient, PERSONA_TITLE_KEYWORDS
from src.integrations.clay import ClayClient

logger = logging.getLogger(__name__)

# --- Classification maps ---

SENIORITY_KEYWORDS: dict[str, list[str]] = {
    # "president" excluded — it's a substring of "Vice President" and would false-match
    "C-Suite": ["chief", "coo", "cfo", "ceo", "cso", "evp", "executive vice president"],
    "SVP": ["svp", "senior vice president", "senior vp"],
    "VP": ["vice president", "vp ", " vp"],  # vice president checked before "vp " substring
    "Director": ["director"],
    "Manager": ["manager", "lead ", "supervisor", "head of"],
    "IC": ["analyst", "specialist", "engineer", "coordinator", "associate"],
}

# Which persona type does a title belong to — checked in priority order
PERSONA_TYPE_RULES: list[tuple[str, list[str]]] = [
    ("FS", ["coo", "cfo", "cso", "chief", "evp", "svp", "president", "vp finance", "director of finance", "vp of finance"]),
    ("IT", ["vp it", "director of it", "vp information", "director of information", "it director", "vp technology", "enterprise architect", "director of technology"]),
    ("Safety", ["safety", "ehs", "environmental health"]),
    ("TDM", ["continuous improvement", " ci ", "automation", "industrial engineer", "lean", "process improvement", "manufacturing engineer", "engineering"]),
    ("ODM", ["operations", "warehouse", "fulfillment", "distribution", "inventory", "supply chain", "icqa", "dc ops", "logistics"]),
]

# Default priority tier per persona type (pre-scoring)
DEFAULT_TIER: dict[str, str] = {
    "FS": "High",
    "TDM": "High",
    "ODM": "Medium",
    "IT": "Medium",
    "Safety": "Low",
}

TIER_ORDER = {"High": 0, "Medium": 1, "Low": 2}


def classify_persona_type(title: str) -> str:
    """Map a job title to a persona type."""
    title_lower = title.lower()
    for ptype, keywords in PERSONA_TYPE_RULES:
        if any(kw in title_lower for kw in keywords):
            return ptype
    return "ODM"  # fallback


def classify_seniority(title: str) -> str:
    """Map a job title to a seniority tier."""
    title_lower = title.lower()
    for seniority, keywords in SENIORITY_KEYWORDS.items():
        if any(kw in title_lower for kw in keywords):
            return seniority
    return "IC"


def assign_lane(seniority: str) -> str:
    """VP and above go to AE lane; Director/Manager to MDR lane."""
    if seniority in ("C-Suite", "SVP", "VP"):
        return "AE"
    return "MDR"


def normalize_apollo_person(raw: dict, session_id: str) -> dict:
    """Flatten an Apollo people record into our internal persona shape."""
    title = raw.get("title") or raw.get("headline") or ""
    persona_type = classify_persona_type(title)
    seniority = classify_seniority(title)
    lane = assign_lane(seniority)
    default_tier = DEFAULT_TIER.get(persona_type, "Medium")

    # C-Suite always High regardless of persona type
    if seniority == "C-Suite":
        default_tier = "High"

    org = raw.get("organization") or {}

    return {
        "id": str(uuid.uuid4()),
        "session_id": session_id,
        "apollo_id": raw.get("id"),
        "first_name": raw.get("first_name") or "",
        "last_name": raw.get("last_name") or "",
        "title": title,
        "email": raw.get("email"),
        "linkedin_url": raw.get("linkedin_url") or raw.get("linkedin_profile_url") or raw.get("li_url"),
        "account_name": org.get("name") or raw.get("organization_name") or "",
        "persona_type": persona_type,
        "seniority": seniority,
        "outreach_lane": lane,
        "priority_score": default_tier,
        "score_reasoning": None,
        "value_driver": None,
        "linkedin_signals": [],
        "gong_hook": None,
        "status": "discovered",
        "discovered_at": datetime.utcnow().isoformat(),
    }


MOCK_PEOPLE = [
    {
        "first_name": "Sarah", "last_name": "Chen",
        "title": "VP of Operations", "email": "s.chen@mockco.com",
        "linkedin_url": "https://linkedin.com/in/sarah-chen-ops",
        "organization": {"name": "{account_name}"},
    },
    {
        "first_name": "Marcus", "last_name": "Williams",
        "title": "Director of Continuous Improvement", "email": "m.williams@mockco.com",
        "linkedin_url": "https://linkedin.com/in/marcus-williams-ci",
        "organization": {"name": "{account_name}"},
    },
    {
        "first_name": "Jennifer", "last_name": "Park",
        "title": "Chief Supply Chain Officer", "email": "j.park@mockco.com",
        "linkedin_url": "https://linkedin.com/in/jennifer-park-csco",
        "organization": {"name": "{account_name}"},
    },
    {
        "first_name": "David", "last_name": "Torres",
        "title": "Director of Inventory Control", "email": "d.torres@mockco.com",
        "linkedin_url": "https://linkedin.com/in/david-torres-inv",
        "organization": {"name": "{account_name}"},
    },
    {
        "first_name": "Aisha", "last_name": "Johnson",
        "title": "Automation Manager", "email": "a.johnson@mockco.com",
        "linkedin_url": "https://linkedin.com/in/aisha-johnson-auto",
        "organization": {"name": "{account_name}"},
    },
    {
        "first_name": "Robert", "last_name": "Kim",
        "title": "VP of IT", "email": "r.kim@mockco.com",
        "linkedin_url": "https://linkedin.com/in/robert-kim-it",
        "organization": {"name": "{account_name}"},
    },
]


class PersonaDiscoveryAgent:
    def __init__(self):
        self.apollo = ApolloClient()
        self.clay = ClayClient()

    def discover(
        self,
        session_id: str,
        account_name: str,
        account_domain: Optional[str] = None,
        persona_filter: Optional[list[str]] = None,
    ) -> list[dict]:
        """
        Search Apollo for people at account_name, classify each into a persona,
        enrich with Clay signals, sort by priority tier, and return max 8.
        """
        started_at = datetime.utcnow()
        logger.info(f"[discovery] Starting for account='{account_name}' session={session_id}")

        # Mock mode — bypass Apollo for local testing
        if settings.MOCK_PERSONAS:
            logger.info("[discovery] MOCK_PERSONAS=true — using mock data")
            raw_people = [
                {**p, "organization": {"name": account_name}}
                for p in MOCK_PEOPLE
            ]
            personas = [normalize_apollo_person(p, session_id) for p in raw_people]
            if persona_filter:
                personas = [p for p in personas if p["persona_type"] in persona_filter]
            logger.info(f"[discovery] Mock complete — {len(personas)} personas")
            return personas

        # 1. Pull people from Apollo
        raw_people = self.apollo.search_people(
            organization_name=account_name,
            organization_domain=account_domain,
            persona_types=persona_filter,
            limit=30,  # over-fetch then trim after dedup + sort
        )

        if not raw_people:
            logger.warning(f"[discovery] Apollo returned 0 results for '{account_name}'")
            return []

        # 2. Normalize + classify each person
        personas = [normalize_apollo_person(p, session_id) for p in raw_people]

        # 3. Apply persona_filter if rep specified one
        if persona_filter:
            personas = [p for p in personas if p["persona_type"] in persona_filter]

        # 4. Deduplicate by name (keep first occurrence)
        seen_names = set()
        unique_personas = []
        for p in personas:
            name_key = f"{p['first_name'].lower()} {p['last_name'].lower()}"
            if name_key not in seen_names:
                seen_names.add(name_key)
                unique_personas.append(p)

        # 5. Sort: High → Medium → Low, then by seniority within tier
        seniority_order = {"C-Suite": 0, "SVP": 1, "VP": 2, "Director": 3, "Manager": 4, "IC": 5}
        unique_personas.sort(key=lambda p: (
            TIER_ORDER.get(p["priority_score"], 1),
            seniority_order.get(p["seniority"], 5),
        ))

        # 6. Cap at 8
        top_personas = unique_personas[:8]

        # 7. Enrich with Clay signals (best-effort, non-blocking)
        for persona in top_personas:
            if persona.get("linkedin_url"):
                signals = self._fetch_clay_signals(persona["linkedin_url"])
                persona["linkedin_signals"] = signals

        duration_ms = int((datetime.utcnow() - started_at).total_seconds() * 1000)
        logger.info(
            f"[discovery] Complete — {len(top_personas)} personas returned in {duration_ms}ms"
        )

        return top_personas

    def _fetch_clay_signals(self, linkedin_url: str) -> list[dict]:
        """Fetch LinkedIn activity signals from Clay. Returns empty list on failure."""
        try:
            return self.clay.get_linkedin_signals(linkedin_url) or []
        except Exception as e:
            logger.warning(f"[discovery] Clay enrichment failed for {linkedin_url}: {e}")
            return []
