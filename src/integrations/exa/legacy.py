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
    "wikipedia.org", "reddit.com", "quora.com",
    "glassdoor.com", "indeed.com", "ziprecruiter.com",
]

# Deep-research query templates per topic
_RESEARCH_QUERIES = {
    "earnings_board": (
        "{company} earnings call investor day strategic priorities supply chain "
        "operations distribution 2024 2025"
    ),
    "earnings_board_alt": (
        "{company} annual report CEO CFO letter shareholders operational priorities "
        "cost reduction automation 2024 2025"
    ),
    "press_releases": (
        "{company} press release announcement new distribution center warehouse "
        "expansion acquisition partnership 2024 2025"
    ),
    "facilities": (
        "{company} number of distribution centers fulfillment centers warehouses "
        "square footage logistics network locations"
    ),
    "facilities_alt": (
        "{company} warehouse network DC count square feet facility expansion "
        "real estate supply chain footprint"
    ),
    "automation": (
        "{company} WMS warehouse management system Blue Yonder Manhattan SAP "
        "automation robotics inventory technology deployment"
    ),
    "triggers": (
        "{company} inventory shrink accuracy audit failure supply chain disruption "
        "new VP operations leadership hire expansion 2024 2025"
    ),
    "hiring": (
        "{company} hiring job opening director manager automation engineer "
        "inventory control continuous improvement supply chain 2024 2025"
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
        num_results: int = 6,
        fetch_top_content: bool = False,
        include_domain: Optional[str] = None,
        also_run_alt: bool = False,
    ) -> list[dict]:
        """
        Run a targeted search for one of the defined research topics.
        topic must be a key in _RESEARCH_QUERIES.
        Returns list of {headline, snippet, url, date, full_content?}.

        fetch_top_content: if True, fetches full article text for the top result.
        include_domain: narrows search to this domain (e.g. the company's IR site).
        also_run_alt: if True, also runs the _alt variant of the query and merges results.
        """
        if not self.api_key:
            return []

        template = _RESEARCH_QUERIES.get(topic, "{company} {topic}")
        tokens = {"company": company_name, "topic": topic}
        if extra_tokens:
            tokens.update(extra_tokens)

        query = template.format(**tokens)
        results = self._search(
            query=query,
            num_results=num_results,
            include_domain=include_domain,
        )

        # Optional alternate query for the same topic (different angle)
        if also_run_alt:
            alt_template = _RESEARCH_QUERIES.get(f"{topic}_alt")
            if alt_template:
                alt_query = alt_template.format(**tokens)
                alt_results = self._search(
                    query=alt_query,
                    num_results=num_results // 2,
                    include_domain=include_domain,
                )
                # Merge, deduplicate by URL
                seen_urls = {r.get("url") for r in results}
                results += [r for r in alt_results if r.get("url") not in seen_urls]

        hits = [
            {
                "headline": r.get("title", ""),
                "snippet": self._best_highlight(r),
                "url": r.get("url", ""),
                "date": r.get("publishedDate", ""),
            }
            for r in results
            if r.get("title") or r.get("highlights")
        ]

        # Fetch full article text for the top result (highest-value content)
        if fetch_top_content and hits:
            top_url = hits[0].get("url", "")
            if top_url:
                content = self.fetch_url_content(top_url, max_chars=5_000)
                if content:
                    hits[0]["full_content"] = content
                    logger.info(f"[exa] Fetched full content for '{topic}' top result: {top_url[:80]}")

        return hits

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
