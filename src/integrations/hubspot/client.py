"""HubSpot Contacts HTTP client (Phase 7 / spec §1.2.1).

Thin httpx wrapper around HubSpot's CRM v3 search endpoint. We deliberately
avoid the official `hubspot` SDK — it's heavy, its exception messages echo
back request bodies (token-leak risk per S1.2.1a), and we only need two
endpoints.

Public API:
- search_contact_by_email(email)        → dict | None
- search_contact_by_name_company(...)   → dict | None  (filtered to confidence >= 0.9)

Both raise httpx.HTTPStatusError on 5xx so callers (contact_check.tag_contacts)
can fall back gracefully per spec §1.2.1.

`_post` retries transparently on 429 (HubSpot's search endpoint cap is ~5
req/sec, far stricter than the 100/10s general limit). We honor the
`Retry-After` header when present and otherwise back off exponentially.
"""
from __future__ import annotations

import time
from typing import Optional

import httpx

SEARCH_PATH = "/crm/v3/objects/contacts/search"
BASE_URL = "https://api.hubapi.com"

RATE_LIMIT_MAX_ATTEMPTS = 3
RATE_LIMIT_INITIAL_BACKOFF_SECONDS = 0.5
RATE_LIMIT_MAX_WAIT_SECONDS = 5.0

# Levenshtein-based confidence threshold for fuzzy name+company match.
NAME_MATCH_CONFIDENCE_THRESHOLD = 0.9


def _levenshtein(a: str, b: str) -> int:
    """Iterative Levenshtein distance. Small strings → O(len(a)*len(b))."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            curr[j] = min(
                curr[j - 1] + 1,        # insertion
                prev[j] + 1,            # deletion
                prev[j - 1] + cost,     # substitution
            )
        prev = curr
    return prev[-1]


def _confidence(query: str, candidate: str) -> float:
    q = (query or "").strip().lower()
    c = (candidate or "").strip().lower()
    if not q and not c:
        return 1.0
    if not q or not c:
        return 0.0
    dist = _levenshtein(q, c)
    longest = max(len(q), len(c))
    return 1.0 - (dist / longest)


class HubSpotContactClient:
    """Thin HubSpot Contacts search client.

    Mockable: tests patch the `_post` method to inject fake responses without
    making real HTTP calls.
    """

    def __init__(self, token: str, *, timeout: float = 10.0):
        self.token = token
        self.timeout = timeout
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    # -- Internal HTTP wrapper (patched in tests) ----------------------------

    def _post(self, path: str, payload: dict) -> httpx.Response:
        url = f"{BASE_URL}{path}"
        backoff = RATE_LIMIT_INITIAL_BACKOFF_SECONDS
        with httpx.Client(timeout=self.timeout) as client:
            for attempt in range(RATE_LIMIT_MAX_ATTEMPTS):
                response = client.post(url, headers=self.headers, json=payload)
                if response.status_code != 429 or attempt == RATE_LIMIT_MAX_ATTEMPTS - 1:
                    return response
                retry_after = response.headers.get("Retry-After")
                try:
                    wait = float(retry_after) if retry_after else backoff
                except ValueError:
                    wait = backoff
                time.sleep(min(wait, RATE_LIMIT_MAX_WAIT_SECONDS))
                backoff *= 2
            return response  # pragma: no cover — loop always returns above

    # -- Public API ----------------------------------------------------------

    def search_contact_by_email(self, email: str) -> Optional[dict]:
        if not email:
            return None
        payload = {
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": "email",
                            "operator": "EQ",
                            "value": email,
                        }
                    ]
                }
            ],
            "properties": ["email", "firstname", "lastname", "company", "jobtitle"],
            "limit": 1,
        }
        response = self._post(SEARCH_PATH, payload)
        response.raise_for_status()
        results = (response.json() or {}).get("results", [])
        if not results:
            return None
        return results[0]

    def search_contact_by_name_company(
        self, first_name: str, last_name: str, company: str
    ) -> Optional[dict]:
        """Search by firstname + lastname + company. Apply Levenshtein-based
        confidence filtering so noisy CONTAINS_TOKEN matches aren't surfaced.
        """
        if not (first_name and last_name):
            return None
        payload = {
            "filterGroups": [
                {
                    "filters": [
                        {"propertyName": "firstname", "operator": "EQ", "value": first_name},
                        {"propertyName": "lastname", "operator": "EQ", "value": last_name},
                    ]
                }
            ],
            "properties": ["email", "firstname", "lastname", "company", "jobtitle"],
            "limit": 5,
        }
        response = self._post(SEARCH_PATH, payload)
        response.raise_for_status()
        results = (response.json() or {}).get("results", [])
        if not results:
            return None

        query_full = f"{first_name} {last_name} {company}"
        best: Optional[dict] = None
        best_conf = 0.0
        for r in results:
            props = r.get("properties", {}) or {}
            cand_full = "{} {} {}".format(
                props.get("firstname", "") or "",
                props.get("lastname", "") or "",
                props.get("company", "") or "",
            )
            conf = _confidence(query_full, cand_full)
            if conf > best_conf:
                best_conf = conf
                best = r

        if best is None or best_conf < NAME_MATCH_CONFIDENCE_THRESHOLD:
            return None
        return best
