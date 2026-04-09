import logging
from typing import Optional

import httpx

from src.config import settings

logger = logging.getLogger(__name__)

# Title keywords per persona type — used to build Apollo search queries
PERSONA_TITLE_KEYWORDS: dict[str, list[str]] = {
    "TDM": [
        "continuous improvement",
        "CI manager",
        "automation manager",
        "industrial engineer",
        "lean manager",
        "process improvement",
        "manufacturing engineer",
        "director of engineering",
        "VP engineering",
        "operations technology",
    ],
    "ODM": [
        "VP operations",
        "director of operations",
        "VP warehouse",
        "director of warehouse",
        "director of fulfillment",
        "VP fulfillment",
        "director of inventory",
        "ICQA",
        "inventory control",
        "supply chain director",
        "director of distribution",
        "DC operations",
    ],
    "FS": [
        "COO",
        "chief operating officer",
        "CFO",
        "chief financial officer",
        "chief supply chain officer",
        "SVP operations",
        "EVP operations",
        "SVP supply chain",
        "VP finance",
        "director of finance",
    ],
    "IT": [
        "VP IT",
        "director of IT",
        "VP information technology",
        "director of information technology",
        "VP technology",
        "IT director",
        "enterprise architect",
    ],
    "Safety": [
        "EHS manager",
        "safety manager",
        "director of safety",
        "VP safety",
        "environmental health",
        "EHS director",
    ],
}


class ApolloClient:
    BASE_URL = "https://api.apollo.io/v1"

    def __init__(self):
        self.api_key = settings.APOLLO_API_KEY
        self.headers = {
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
        }

    def search_people(
        self,
        organization_name: str,
        persona_types: Optional[list[str]] = None,
        limit: int = 25,
    ) -> list[dict]:
        """Search for people at a company filtered by persona type title keywords."""
        if not self.api_key:
            logger.warning("APOLLO_API_KEY not set — returning empty results")
            return []

        types_to_search = persona_types or list(PERSONA_TITLE_KEYWORDS.keys())
        all_titles = []
        for ptype in types_to_search:
            all_titles.extend(PERSONA_TITLE_KEYWORDS.get(ptype, []))

        try:
            response = httpx.post(
                f"{self.BASE_URL}/mixed_people/search",
                headers=self.headers,
                json={
                    "api_key": self.api_key,
                    "q_organization_name": organization_name,
                    "person_titles": all_titles,
                    "page": 1,
                    "per_page": limit,
                },
                timeout=15,
            )
            response.raise_for_status()
            data = response.json()
            people = data.get("people", [])
            logger.info(f"Apollo returned {len(people)} results for '{organization_name}'")
            return people
        except httpx.HTTPStatusError as e:
            logger.error(f"Apollo HTTP error: {e.response.status_code} — {e.response.text[:200]}")
            return []
        except Exception as e:
            logger.error(f"Apollo search failed: {e}")
            return []

    def get_person(self, person_id: str) -> Optional[dict]:
        """Fetch full person record by Apollo person ID."""
        if not self.api_key:
            return None
        try:
            response = httpx.get(
                f"{self.BASE_URL}/people/{person_id}",
                headers=self.headers,
                params={"api_key": self.api_key},
                timeout=10,
            )
            response.raise_for_status()
            return response.json().get("person")
        except Exception as e:
            logger.error(f"Apollo get_person failed for {person_id}: {e}")
            return None
