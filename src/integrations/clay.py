import httpx
from typing import Optional
from src.config import settings


class ClayClient:
    BASE_URL = "https://api.clay.com/v1"

    def __init__(self):
        self.api_key = settings.CLAY_API_KEY
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def find_company(self, account_name: str) -> Optional[dict]:
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
        except Exception:
            return None

    def find_people(self, company_name: str, title_keywords: list[str], limit: int = 8) -> list[dict]:
        if not self.api_key:
            return []
        try:
            response = httpx.post(
                f"{self.BASE_URL}/people/search",
                headers=self.headers,
                json={
                    "company_name": company_name,
                    "title_keywords": title_keywords,
                    "limit": limit,
                },
                timeout=15,
            )
            response.raise_for_status()
            return response.json().get("data", [])
        except Exception:
            return []
