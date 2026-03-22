import httpx
from typing import Optional
from src.config import settings


class HubSpotClient:
    BASE_URL = "https://api.hubapi.com"

    def __init__(self):
        self.token = settings.HUBSPOT_ACCESS_TOKEN
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def find_company(self, account_name: str) -> Optional[dict]:
        if not self.token:
            return None
        try:
            response = httpx.post(
                f"{self.BASE_URL}/crm/v3/objects/companies/search",
                headers=self.headers,
                json={
                    "filterGroups": [{
                        "filters": [{
                            "propertyName": "name",
                            "operator": "CONTAINS_TOKEN",
                            "value": account_name,
                        }]
                    }],
                    "properties": ["name", "domain", "description", "industry", "numberofemployees"],
                    "limit": 1,
                },
                timeout=10,
            )
            response.raise_for_status()
            results = response.json().get("results", [])
            if not results:
                return None
            props = results[0].get("properties", {})
            return {
                "id": results[0].get("id"),
                "name": props.get("name"),
                "domain": props.get("domain"),
                "description": props.get("description"),
                "industry": props.get("industry"),
            }
        except Exception:
            return None

    def log_activity(self, contact_id: str, note: str) -> bool:
        if not self.token:
            return False
        try:
            response = httpx.post(
                f"{self.BASE_URL}/crm/v3/objects/notes",
                headers=self.headers,
                json={
                    "properties": {
                        "hs_note_body": note,
                        "hs_timestamp": None,
                    },
                    "associations": [{
                        "to": {"id": contact_id},
                        "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 202}],
                    }],
                },
                timeout=10,
            )
            response.raise_for_status()
            return True
        except Exception:
            return False
