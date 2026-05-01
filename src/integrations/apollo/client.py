"""Apollo v1 contact search client (Phase 12 / spec §1.3).

Thin httpx wrapper around Apollo's `/v1/mixed_people/search` endpoint.
Mockable by patching `_post` (same pattern as `HubSpotContactClient._post`).

Why a new client?
-----------------
The legacy `ApolloClient` (now in `legacy.py`) carries the prototype's
own persona taxonomy (TDM/ODM/FS/IT/Safety) and is wired to a different
endpoint (`/api/v1/mixed_people/api_search`). The new bot uses the four
ICP personas defined in spec §1.3 and surfaces a clean `(company,
title_keywords)` signature for the Phase 13 contact pipeline.

Security gates
--------------
- S1.2.1a — All exceptions caught and logged via `safe_log_exception`
  (type-name only). The token never lands in stringified exceptions.
- The client never raises out — non-2xx and transport errors return [].
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from src.security.exception_logger import safe_log_exception

logger = logging.getLogger(__name__)

BASE_URL = "https://api.apollo.io"
SEARCH_PATH = "/v1/mixed_people/search"


class ApolloContactClient:
    """Thin Apollo people-search client.

    Mockable: tests patch the `_post` method to inject fake responses
    without making real HTTP calls.
    """

    def __init__(self, api_key: str, *, timeout: float = 15.0):
        self.api_key = api_key
        self.timeout = timeout
        # Apollo uses X-Api-Key, NOT Authorization: Bearer.
        self.headers = {
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
            "X-Api-Key": api_key,
        }

    # -- Internal HTTP wrapper (patched in tests) ----------------------------

    def _post(self, path: str, payload: dict) -> httpx.Response:
        url = f"{BASE_URL}{path}"
        with httpx.Client(timeout=self.timeout) as client:
            return client.post(url, headers=self.headers, json=payload)

    # -- Public API ----------------------------------------------------------

    def search_contacts_by_company_and_titles(
        self,
        company_name: str,
        title_keywords: List[str],
        *,
        limit: int = 25,
    ) -> List[Dict[str, Any]]:
        """Search Apollo for people at `company_name` whose titles match
        any of `title_keywords`.

        Returns a list of contact dicts shaped for the Phase 7
        `tag_contacts` pipeline:
            {first_name, last_name, email, company, title}

        Failure modes (any) → [] + safe-logged exception. Never raises.
        """
        if not company_name or not title_keywords:
            return []

        payload = {
            "q_organization_name": company_name,
            "person_titles": list(title_keywords),
            "page": 1,
            "per_page": int(limit),
        }

        try:
            response = self._post(SEARCH_PATH, payload)
            response.raise_for_status()
            body = response.json() or {}
        except Exception as e:  # noqa: BLE001 — graceful fallback
            safe_log_exception(logger, e, "apollo people search failed")
            return []

        people = body.get("people") or []
        return [_normalize_person(p) for p in people]


def _normalize_person(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten an Apollo person record into the contact dict shape the
    Phase 7 tagger / Slack renderer consume."""
    organization = raw.get("organization") or {}
    company = (
        organization.get("name")
        if isinstance(organization, dict) else None
    ) or raw.get("organization_name") or ""

    return {
        "first_name": raw.get("first_name") or "",
        "last_name": raw.get("last_name") or "",
        "email": raw.get("email") or "",
        "title": raw.get("title") or "",
        "company": company,
        # Carry Apollo id forward in case downstream code wants it
        "apollo_id": raw.get("id"),
    }
