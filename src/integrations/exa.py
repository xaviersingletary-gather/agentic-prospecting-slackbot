import logging
from typing import Optional

import httpx

from src.config import settings

logger = logging.getLogger(__name__)

EXA_SEARCH_URL = "https://api.exa.ai/search"
EXA_CONTENTS_URL = "https://api.exa.ai/contents"

# Domains to exclude — avoid generic aggregators that won't have account-specific signals
EXCLUDE_DOMAINS = [
    "facebook.com", "twitter.com", "x.com",
    "wikipedia.org", "crunchbase.com", "bloomberg.com",
]

# Deep-research query templates per topic
_RESEARCH_QUERIES = {
    "earnings_board": (
        "{company} earnings call investor day strategic priorities "
        "annual report board initiatives 2024 2025"
    ),
    "press_releases": (
        "{company} press release announcement expansion acquisition "
        "new facility investment 2024 2025"
    ),
    "facilities": (
        "{company} distribution center warehouse DC locations count "
        "square feet logistics network"
    ),
    "automation": (
        "{company} WMS warehouse management system automation robotics "
        "inventory technology deployment Blue Yonder Manhattan SAP"
    ),
    "triggers": (
        "{company} inventory shrinkage audit leadership change "
        "supply chain investment warehouse technology job posting 2024 2025"
    ),
    "contact": (
        '"{first_name} {last_name}" {company} {title} '
        "LinkedIn announcement interview conference speaking"
    ),
}


class ExaClient:
    def __init__(self):
        self.api_key = settings.EXA_API_KEY

    # ------------------------------------------------------------------
    # Legacy method — kept for backward compat with scorer/main
    # ------------------------------------------------------------------

    def research_account(
        self,
        account_name: str,
        account_domain: Optional[str] = None,
    ) -> list[dict]:
        """
        Quick two-query account research returning compact signals.
        Used by the scorer for Exa hooks (legacy path).
        """
        if not self.api_key:
            logger.warning("[exa] EXA_API_KEY not set — skipping account research")
            return []

        signals = []
        queries = [
            {
                "type": "operations_news",
                "query": (
                    f"{account_name} warehouse distribution center operations "
                    "inventory automation expansion"
                ),
            },
            {
                "type": "hiring_signals",
                "query": (
                    f"{account_name} hiring warehouse automation inventory "
                    "director manager job posting"
                ),
            },
        ]

        for q in queries:
            results = self._search(
                query=q["query"],
                num_results=3,
                include_domain=account_domain,
            )
            for r in results:
                signals.append({
                    "type": q["type"],
                    "headline": r.get("title", ""),
                    "snippet": self._best_highlight(r),
                    "url": r.get("url", ""),
                    "date": r.get("publishedDate"),
                })

        logger.info(f"[exa] research_account for '{account_name}' returned {len(signals)} signals")
        return signals

    # ------------------------------------------------------------------
    # Deep research methods — used by Company Researcher Agent
    # ------------------------------------------------------------------

    def search_topic(
        self,
        company_name: str,
        topic: str,
        extra_tokens: Optional[dict] = None,
        num_results: int = 4,
    ) -> list[dict]:
        """
        Run a targeted search for one of the defined research topics.
        topic must be a key in _RESEARCH_QUERIES.
        Returns list of {headline, snippet, url, date}.
        """
        if not self.api_key:
            return []

        template = _RESEARCH_QUERIES.get(topic, "{company} {topic}")
        tokens = {"company": company_name, "topic": topic}
        if extra_tokens:
            tokens.update(extra_tokens)

        query = template.format(**tokens)
        results = self._search(query=query, num_results=num_results)

        return [
            {
                "headline": r.get("title", ""),
                "snippet": self._best_highlight(r),
                "url": r.get("url", ""),
                "date": r.get("publishedDate", ""),
            }
            for r in results
            if r.get("title") or r.get("highlights")
        ]

    def fetch_url_content(self, url: str, max_chars: int = 20_000) -> str:
        """
        Fetch the full text content of a specific URL via Exa's contents endpoint.
        Returns empty string on failure.
        """
        if not self.api_key:
            return ""
        try:
            resp = httpx.post(
                EXA_CONTENTS_URL,
                headers={
                    "x-api-key": self.api_key,
                    "Content-Type": "application/json",
                },
                json={
                    "ids": [url],
                    "text": {"maxCharacters": max_chars},
                },
                timeout=20,
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])
            if results:
                return results[0].get("text", "") or ""
        except Exception as e:
            logger.warning(f"[exa] fetch_url_content failed for {url}: {e}")
        return ""

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _search(
        self,
        query: str,
        num_results: int = 3,
        include_domain: Optional[str] = None,
    ) -> list[dict]:
        """Run a single Exa search and return raw result list."""
        try:
            payload: dict = {
                "query": query,
                "type": "auto",
                "numResults": num_results,
                "excludeDomains": EXCLUDE_DOMAINS,
                "contents": {
                    "highlights": {
                        "maxCharacters": 400,
                        "query": query,
                    },
                },
            }

            if include_domain:
                payload["includeDomains"] = [include_domain]

            response = httpx.post(
                EXA_SEARCH_URL,
                headers={
                    "x-api-key": self.api_key,
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=15,
            )
            response.raise_for_status()
            return response.json().get("results", [])

        except httpx.HTTPStatusError as e:
            logger.error(f"[exa] HTTP error: {e.response.status_code} — {e.response.text[:200]}")
            return []
        except Exception as e:
            logger.error(f"[exa] Search failed: {e}")
            return []

    @staticmethod
    def _best_highlight(result: dict) -> str:
        """Return the most relevant highlight snippet, or fall back to empty string."""
        highlights = result.get("highlights") or []
        if highlights:
            return highlights[0][:400]
        text = result.get("text") or ""
        return text[:200]
