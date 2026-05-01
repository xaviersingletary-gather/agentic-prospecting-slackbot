"""Exa search HTTP client (Phase 11 / spec §1.4).

Minimal sync httpx wrapper around Exa's `/search` endpoint, modeled on
the HubSpot Phase 7 client (no SDK, easy to mock, exception messages
never leak token material).

Each result URL is validated through `assert_safe_url` (spec gate S1.4
SSRF guard) before being surfaced to callers. Results pointing at
private / link-local / metadata-service IPs are silently dropped.

Public surface:
- `ExaSearchClient(api_key, timeout=15)`
- `client.search(query, num_results=10) -> list[dict]`
  Each dict: {"title", "url", "snippet", "published_date"}.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from src.security.url_guard import BlockedUrlError, assert_safe_url

logger = logging.getLogger(__name__)

EXA_SEARCH_URL = "https://api.exa.ai/search"

# Generic noise we don't want in research output
_EXCLUDE_DOMAINS: List[str] = [
    "facebook.com", "twitter.com", "x.com",
    "wikipedia.org", "reddit.com", "quora.com",
    "glassdoor.com", "indeed.com", "ziprecruiter.com",
]


class ExaSearchClient:
    """Thin httpx wrapper around Exa /search.

    Mockable: tests patch `_post` to inject fake responses. We never
    follow redirects automatically, and we set a tight default timeout
    so a slow Exa never blocks the Slack 3-second ack budget.
    """

    def __init__(self, api_key: str, *, timeout: float = 15.0):
        self.api_key = api_key or ""
        self.timeout = timeout
        self.headers: Dict[str, str] = {
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
        }

    # -- Internal HTTP wrapper (patched in tests) --------------------------
    def _post(self, url: str, payload: dict) -> httpx.Response:
        with httpx.Client(timeout=self.timeout) as client:
            return client.post(url, headers=self.headers, json=payload)

    # -- Public API --------------------------------------------------------
    def search(
        self,
        query: str,
        *,
        num_results: int = 10,
        include_domain: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Run a single Exa search.

        Returns a list of {"title", "url", "snippet", "published_date"}
        dicts, with every URL having passed the SSRF guard. Returns []
        on missing API key, HTTP error, or any unexpected exception —
        the runner above us must keep going on failure.
        """
        if not self.api_key:
            logger.info("[exa] EXA_API_KEY not set — skipping search")
            return []

        payload: Dict[str, Any] = {
            "query": query,
            "type": "auto",
            "numResults": num_results,
            "excludeDomains": _EXCLUDE_DOMAINS,
            "contents": {
                "highlights": {"maxCharacters": 400, "query": query},
            },
        }
        if include_domain:
            payload["includeDomains"] = [include_domain]

        try:
            response = self._post(EXA_SEARCH_URL, payload)
            response.raise_for_status()
            raw = response.json().get("results", []) or []
        except httpx.HTTPStatusError as e:
            # Avoid logging response body — may echo our token / query
            logger.error(
                "[exa] HTTP %s on /search",
                getattr(e.response, "status_code", "?"),
            )
            return []
        except httpx.HTTPError as e:
            logger.error("[exa] transport error: %s", type(e).__name__)
            return []
        except Exception as e:
            logger.error("[exa] unexpected error: %s", type(e).__name__)
            return []

        out: List[Dict[str, Any]] = []
        for r in raw:
            url = (r.get("url") or "").strip()
            if not url:
                continue
            try:
                assert_safe_url(url)
            except BlockedUrlError:
                logger.warning("[exa] dropped result — URL blocked by SSRF guard")
                continue
            highlights = r.get("highlights") or []
            snippet = highlights[0] if highlights else (r.get("text") or "")
            out.append({
                "title": (r.get("title") or "").strip(),
                "url": url,
                "snippet": (snippet or "")[:400],
                "published_date": r.get("publishedDate") or "",
            })
        return out
