import logging
from typing import Optional

import httpx

from src.config import settings

logger = logging.getLogger(__name__)

EXA_URL = "https://api.exa.ai/search"

# Domains to exclude — avoid generic aggregators that won't have account-specific signals
EXCLUDE_DOMAINS = [
    "linkedin.com", "facebook.com", "twitter.com", "x.com",
    "wikipedia.org", "crunchbase.com", "bloomberg.com",
]


class ExaClient:
    def __init__(self):
        self.api_key = settings.EXA_API_KEY

    def research_account(
        self,
        account_name: str,
        account_domain: Optional[str] = None,
    ) -> list[dict]:
        """
        Run targeted searches for an account and return a compact list of signals.
        Each signal: {"type": str, "headline": str, "url": str, "date": str | None}

        Returns empty list if EXA_API_KEY is not set or all searches fail.
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

        logger.info(
            f"[exa] Research for '{account_name}' returned {len(signals)} signals"
        )
        return signals

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
                        "maxCharacters": 300,
                        "query": query,
                    },
                },
            }

            # If we have the company domain, bias results toward it
            if include_domain:
                payload["includeDomains"] = [include_domain]

            response = httpx.post(
                EXA_URL,
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
            return highlights[0][:300]
        text = result.get("text") or ""
        return text[:200]
