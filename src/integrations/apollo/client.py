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
import re
from typing import Any, Dict, List, Optional

import httpx

from src.security.exception_logger import safe_log_exception


def _derive_domain(company_name: str) -> str:
    """Best-guess domain from a company name. Mirrors the legacy
    prototype's resolver — drops common legal/structural suffixes,
    lowercases, strips non-alphanumeric, appends `.com`.

    Examples: "John Deere" → "johndeere.com",
              "CEVA Logistics" → "cevalogistics.com",
              "AbbVie Inc" → "abbvie.com"
    """
    name = re.sub(
        r"\b(inc|llc|corp|co|ltd|limited|incorporated|group|holdings|enterprises|company)\b",
        "",
        company_name,
        flags=re.IGNORECASE,
    )
    name = re.sub(r"[^a-z0-9]", "", name.lower())
    if not name:
        return company_name.lower().replace(" ", "") + ".com"
    return f"{name}.com"

logger = logging.getLogger(__name__)

# Match the endpoint shape the legacy (working) prototype client used —
# Apollo's documented `/v1/mixed_people/search` returns 422 for our
# request shapes; `/api/v1/mixed_people/api_search` accepts the same
# filters the legacy client was sending in production.
BASE_URL = "https://api.apollo.io/api/v1"
SEARCH_PATH = "/mixed_people/api_search"
BULK_MATCH_PATH = "/people/bulk_match"

# Apollo's bulk_match accepts up to 10 IDs per call.
ENRICH_BATCH_SIZE = 10


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
        domain: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Search Apollo for people at `company_name` whose titles match
        any of `title_keywords`.

        Strategy (matches the legacy prototype that has been working):
        1. Resolve a domain — caller-provided or derived from the
           company name — and search via `q_organization_domains_list`.
           Domain-based is the only Apollo filter that reliably returns
           hits for org-name searches; `q_keywords` is flaky.
        2. If the domain search returns 0 people (wrong guess, e.g.
           "AbbVie" → `abbvie.com` is right but "Sysco Foods" → wrong),
           retry once with `q_keywords` as a fallback.

        Returns a list of contact dicts shaped for the Phase 7
        `tag_contacts` pipeline:
            {first_name, last_name, email, title, linkedin_url, company}

        Failure modes (any) → [] + safe-logged exception. Never raises.
        """
        if not company_name or not title_keywords:
            return []

        resolved_domain = domain or _derive_domain(company_name)
        people = self._search(
            company_name,
            title_keywords,
            limit,
            filter_kind="domain",
            filter_value=resolved_domain,
        )
        if not people:
            logger.info(
                "[apollo] domain search for %r (domain=%s) returned 0; "
                "retrying with q_keywords",
                company_name, resolved_domain,
            )
            people = self._search(
                company_name,
                title_keywords,
                limit,
                filter_kind="keywords",
                filter_value=company_name,
            )

        logger.info(
            "[apollo] %r → %d contacts surfaced",
            company_name, len(people),
        )
        # Apollo's search returns redacted records — first name + title
        # only on most accounts. Enrich via /people/bulk_match to unlock
        # last_name, work email, and linkedin_url. One credit per match;
        # failures are non-fatal (we still render whatever the search
        # gave us).
        if people:
            people = self._enrich_people(people)

        return [_normalize_person(p) for p in people]

    def _enrich_people(
        self, people: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Call `/people/bulk_match` in batches of 10 and merge the
        enriched fields (last_name, email, linkedin_url) back into the
        search records by Apollo ID. Mutates the input list in place
        and also returns it.

        `reveal_personal_emails=False` keeps us on work emails only —
        spec is B2B only.
        """
        enrichable = [p for p in people if p.get("id")]
        if not enrichable:
            return people

        id_to_person = {p["id"]: p for p in enrichable}
        total_emails = 0
        total_linkedin = 0

        for i in range(0, len(enrichable), ENRICH_BATCH_SIZE):
            batch = enrichable[i : i + ENRICH_BATCH_SIZE]
            payload = {
                "details": [{"id": p["id"]} for p in batch],
                "reveal_personal_emails": False,
            }
            try:
                response = self._post(BULK_MATCH_PATH, payload)
                if response.status_code >= 400:
                    body_preview = (response.text or "")[:300]
                    logger.error(
                        "[apollo] bulk_match %s — body=%r",
                        response.status_code, body_preview,
                    )
                    continue  # skip this batch; keep going on others
                data = response.json() or {}
            except Exception as e:  # noqa: BLE001
                safe_log_exception(logger, e, "apollo bulk_match failed")
                continue

            matches = data.get("matches") or data.get("people") or []
            for match in matches:
                pid = match.get("id")
                if not pid or pid not in id_to_person:
                    continue
                target = id_to_person[pid]
                if match.get("last_name"):
                    target["last_name"] = match["last_name"]
                if match.get("first_name") and not target.get("first_name"):
                    target["first_name"] = match["first_name"]
                if match.get("linkedin_url"):
                    target["linkedin_url"] = match["linkedin_url"]
                    total_linkedin += 1
                email = match.get("email") or match.get("work_email")
                if email:
                    target["email"] = email
                    total_emails += 1

        logger.info(
            "[apollo] enrichment: %d/%d emails, %d/%d linkedin",
            total_emails, len(enrichable),
            total_linkedin, len(enrichable),
        )
        return people

    def _search(
        self,
        company_name: str,
        title_keywords: List[str],
        limit: int,
        *,
        filter_kind: str,
        filter_value: str,
    ) -> List[Dict[str, Any]]:
        """Single Apollo search call. Returns the raw `people` list."""
        payload: Dict[str, Any] = {
            "person_titles": list(title_keywords),
            "page": 1,
            "per_page": int(limit),
        }
        if filter_kind == "domain":
            payload["q_organization_domains_list"] = [filter_value]
        else:
            payload["q_keywords"] = filter_value

        try:
            response = self._post(SEARCH_PATH, payload)
            if response.status_code >= 400:
                body_preview = (response.text or "")[:300]
                logger.error(
                    "[apollo] %s on %s (filter=%s) — body=%r",
                    response.status_code, SEARCH_PATH, filter_kind, body_preview,
                )
                response.raise_for_status()
            body = response.json() or {}
        except Exception as e:  # noqa: BLE001 — graceful fallback
            safe_log_exception(logger, e, "apollo people search failed")
            return []
        return body.get("people") or []


def _normalize_person(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten an Apollo person record into the contact dict shape the
    Phase 7 tagger / Slack renderer consume.

    Apollo returns names split into `first_name` / `last_name` plus a
    combined `name`. We fall back to `name` when the split fields come
    back empty (happens for some org-search results). LinkedIn URL is
    pulled from `linkedin_url` and surfaced separately so the renderer
    can show a clickable link.

    Email is surfaced verbatim — Apollo's free/limited-credit tier
    returns the literal placeholder `email_not_unlocked@domain.com`,
    which the renderer detects and hides rather than displaying as a
    real address.
    """
    organization = raw.get("organization") or {}
    company = (
        organization.get("name")
        if isinstance(organization, dict) else None
    ) or raw.get("organization_name") or ""

    first = (raw.get("first_name") or "").strip()
    last = (raw.get("last_name") or "").strip()
    if not first and not last:
        full = (raw.get("name") or "").strip()
        if full:
            parts = full.split(" ", 1)
            first = parts[0]
            last = parts[1] if len(parts) > 1 else ""

    return {
        "first_name": first,
        "last_name": last,
        "email": raw.get("email") or "",
        "title": raw.get("title") or "",
        "linkedin_url": raw.get("linkedin_url") or "",
        "company": company,
        # Carry Apollo id forward in case downstream code wants it
        "apollo_id": raw.get("id"),
    }
