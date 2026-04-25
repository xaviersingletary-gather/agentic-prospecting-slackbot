import logging
import re
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
    BASE_URL = "https://api.apollo.io/api/v1"

    def __init__(self):
        self.api_key = settings.APOLLO_API_KEY
        self.headers = {
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
            "X-Api-Key": self.api_key,
        }

    def search_people(
        self,
        organization_name: str,
        organization_domain: Optional[str] = None,
        persona_types: Optional[list[str]] = None,
        limit: int = 25,
    ) -> list[dict]:
        """
        People-search flow:
          1. Resolve domain: use provided domain, or derive one from company name
          2. Search people via q_organization_domains_list (avoids plan-gated org endpoint)
          3. Enrich work emails for those Apollo signals have an email
        """
        if not self.api_key:
            logger.warning("[apollo] APOLLO_API_KEY not set — returning empty results")
            return []

        types_to_search = persona_types or list(PERSONA_TITLE_KEYWORDS.keys())
        all_titles = []
        for ptype in types_to_search:
            all_titles.extend(PERSONA_TITLE_KEYWORDS.get(ptype, []))

        domain = organization_domain or self._derive_domain(organization_name)
        logger.info(f"[apollo] Using domain '{domain}' for '{organization_name}'")

        people = self._search_people(titles=all_titles, limit=limit, domain=domain)
        logger.info(f"[apollo] People search: {len(people)} results for '{organization_name}'")
        if people:
            sample = people[0]
            logger.info(f"[apollo] Sample person keys: {list(sample.keys())}")
            logger.info(
                f"[apollo] Sample person: first={sample.get('first_name')!r} "
                f"last={sample.get('last_name')!r} "
                f"email={sample.get('email')!r} "
                f"email_status={sample.get('email_status')!r} "
                f"linkedin={sample.get('linkedin_url')!r}"
            )

        if people:
            people = self._enrich_emails(people)

        return people

    def _search_people(
        self,
        titles: list[str],
        limit: int,
        domain: Optional[str] = None,
        org_id: Optional[str] = None,
    ) -> list[dict]:
        """Call /mixed_people/api_search filtered by domain or org ID."""
        payload: dict = {
            "person_titles": titles,
            "page": 1,
            "per_page": min(limit, 100),
        }
        if domain:
            payload["q_organization_domains_list"] = [domain]
        elif org_id:
            payload["organization_ids"] = [org_id]

        try:
            resp = httpx.post(
                f"{self.BASE_URL}/mixed_people/api_search",
                headers=self.headers,
                json=payload,
                timeout=20,
            )
            resp.raise_for_status()
            return resp.json().get("people", [])
        except httpx.HTTPStatusError as e:
            logger.error(
                f"[apollo] People search HTTP {e.response.status_code}: {e.response.text[:200]}"
            )
            return []
        except Exception as e:
            logger.error(f"[apollo] People search failed: {e}")
            return []

    @staticmethod
    def _derive_domain(company_name: str) -> str:
        """
        Derive a best-guess domain from a company name when no domain is provided.
        Strips common legal suffixes, lowercases, removes non-alphanumeric chars.
        Examples: "John Deere" → "johndeere.com", "AbbVie Inc" → "abbvie.com"
        """
        name = re.sub(
            r"\b(inc|llc|corp|co|ltd|limited|incorporated|group|holdings|enterprises|company)\b",
            "",
            company_name,
            flags=re.IGNORECASE,
        )
        name = re.sub(r"[^a-z0-9]", "", name.lower())
        return f"{name}.com" if name else company_name.lower().replace(" ", "") + ".com"

    def _enrich_emails(self, people: list[dict]) -> list[dict]:
        """
        Batch-enrich work emails via /people/bulk_match (API max: 10 per call).
        Enriches all contacts — search results don't reliably include email_status.
        Merges email back into the original list in place.
        """
        enrichable = [p for p in people if p.get("id")]
        if not enrichable:
            return people

        logger.info(f"[apollo] Enriching emails for {len(enrichable)} of {len(people)} people")
        id_to_person = {p["id"]: p for p in people if p.get("id")}

        for i in range(0, len(enrichable), 10):
            batch = enrichable[i : i + 10]
            details = [{"id": p["id"]} for p in batch]
            try:
                resp = httpx.post(
                    f"{self.BASE_URL}/people/bulk_match",
                    headers=self.headers,
                    json={"details": details, "reveal_personal_emails": False},
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()
                matches = data.get("matches") or data.get("people") or []
                if matches:
                    sample_match = matches[0]
                    logger.info(f"[apollo] Enrichment sample keys: {list(sample_match.keys())}")
                    logger.info(
                        f"[apollo] Enrichment sample: first={sample_match.get('first_name')!r} "
                        f"last={sample_match.get('last_name')!r} "
                        f"email={sample_match.get('email')!r} "
                        f"linkedin={sample_match.get('linkedin_url')!r}"
                    )
                for match in matches:
                    pid = match.get("id")
                    if pid and pid in id_to_person:
                        if match.get("last_name"):
                            id_to_person[pid]["last_name"] = match["last_name"]
                        if match.get("linkedin_url"):
                            id_to_person[pid]["linkedin_url"] = match["linkedin_url"]
                        email = match.get("email") or match.get("work_email")
                        if email:
                            id_to_person[pid]["email"] = email
                emails_found = sum(1 for m in matches if m.get("email") or m.get("work_email"))
                logger.info(f"[apollo] Enrichment batch {i // 10 + 1}: {len(matches)} matches, {emails_found} emails")
            except Exception as e:
                logger.warning(f"[apollo] Enrichment batch {i // 10 + 1} failed (non-fatal): {e}")

        return people

    def get_person(self, person_id: str) -> Optional[dict]:
        """Fetch full person record by Apollo person ID."""
        if not self.api_key:
            return None
        try:
            resp = httpx.get(
                f"{self.BASE_URL}/people/{person_id}",
                headers=self.headers,
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json().get("person")
        except Exception as e:
            logger.error(f"[apollo] get_person failed for {person_id}: {e}")
            return None
