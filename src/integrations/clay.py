import logging
from typing import Optional

import httpx

from src.config import settings

logger = logging.getLogger(__name__)


class ClayClient:
    BASE_URL = "https://api.clay.com/v1"

    def __init__(self):
        self.api_key = settings.CLAY_API_KEY
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def find_company(self, account_name: str) -> Optional[dict]:
        """Look up company domain + description by name."""
        if not self.api_key:
            return None
        try:
            response = httpx.get(
                f"{self.BASE_URL}/companies/search",
                headers=self.headers,
                params={"name": account_name, "limit": 1},
                timeout=10,
            )
            response.raise_for_status()
            results = response.json().get("data", [])
            if not results:
                return None
            company = results[0]
            return {
                "name": company.get("name"),
                "domain": company.get("domain"),
                "description": company.get("description"),
                "industry": company.get("industry"),
            }
        except Exception as e:
            logger.warning(f"Clay find_company failed for '{account_name}': {e}")
            return None

    def get_linkedin_signals(self, linkedin_url: str) -> list[dict]:
        """
        Fetch recent LinkedIn activity signals for a person.
        Returns a list of signal dicts: {type, content, date, relevance_score}
        Returns empty list if Clay key not set or request fails.
        """
        if not self.api_key:
            return []
        try:
            response = httpx.post(
                f"{self.BASE_URL}/enrich/linkedin-activity",
                headers=self.headers,
                json={"linkedin_url": linkedin_url},
                timeout=12,
            )
            response.raise_for_status()
            data = response.json().get("data", {})
            raw_signals = data.get("recent_activity", [])
            return [
                {
                    "type": s.get("type", "post"),
                    "content": s.get("text", "")[:300],
                    "date": s.get("date"),
                    "relevance_score": s.get("relevance_score", 0),
                }
                for s in raw_signals
            ]
        except Exception as e:
            logger.warning(f"Clay get_linkedin_signals failed for {linkedin_url}: {e}")
            return []
