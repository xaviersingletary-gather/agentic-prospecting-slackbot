"""
HTML document fetching and section extraction utilities.
Used by the Company Researcher agent to pull 10-K and press release content.
"""
import logging
import re
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Section header patterns for 10-K targeting
_SECTION_PATTERNS = {
    "risk_factors": [
        r"item\s+1a[\.\s]",
        r"risk\s+factors",
    ],
    "mda": [
        r"item\s+7[\.\s]",
        r"management.{0,5}s\s+discussion\s+and\s+analysis",
        r"management.{0,5}s\s+discussion",
    ],
    "business": [
        r"item\s+1[\.\s](?!a)",
        r"business\s+overview",
        r"description\s+of\s+business",
    ],
    "capex_mentions": [
        r"capital\s+expenditure",
        r"capital\s+investments?",
        r"automation\s+investment",
        r"technology\s+investment",
    ],
}

_HEADERS = {
    "User-Agent": "GatherAI-Prospecting-Bot/1.0 research@gather.ai",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def fetch_html(url: str, timeout: int = 15) -> Optional[str]:
    """Fetch raw HTML from a URL. Returns None on failure."""
    try:
        resp = httpx.get(url, headers=_HEADERS, timeout=timeout, follow_redirects=True)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        logger.warning(f"[document_fetcher] fetch_html failed for {url}: {e}")
        return None


def html_to_text(html: str, max_chars: int = 50_000) -> str:
    """Strip HTML tags and normalize whitespace. Returns plain text."""
    # Remove script/style blocks entirely
    text = re.sub(r"<(script|style)[^>]*>.*?</(script|style)>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    # Strip remaining tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Decode common HTML entities
    text = (
        text.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&nbsp;", " ")
        .replace("&#160;", " ")
        .replace("&ldquo;", '"')
        .replace("&rdquo;", '"')
        .replace("&lsquo;", "'")
        .replace("&rsquo;", "'")
        .replace("&mdash;", "—")
        .replace("&ndash;", "–")
    )
    # Normalize whitespace
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:max_chars]


def extract_10k_sections(html: str, chars_per_section: int = 3_000) -> dict:
    """
    Extract targeted 10-K sections from HTML text.
    Returns dict of {section_name: extracted_text}.
    Prioritizes MD&A, Risk Factors, Business description, and capex mentions.
    """
    plain = html_to_text(html, max_chars=200_000)
    plain_lower = plain.lower()
    sections = {}

    for section_name, patterns in _SECTION_PATTERNS.items():
        for pattern in patterns:
            match = re.search(pattern, plain_lower)
            if match:
                start = match.start()
                end = min(start + chars_per_section, len(plain))
                sections[section_name] = plain[start:end].strip()
                break  # take first match per section type

    return sections


def extract_relevant_text(html: str, keywords: list, context_chars: int = 500, max_hits: int = 5) -> str:
    """
    Find sentences/paragraphs containing given keywords and return them with context.
    Useful for extracting capex, automation, or inventory mentions from long documents.
    """
    plain = html_to_text(html, max_chars=200_000)
    plain_lower = plain.lower()
    hits = []

    for kw in keywords:
        kw_lower = kw.lower()
        pos = 0
        while len(hits) < max_hits:
            idx = plain_lower.find(kw_lower, pos)
            if idx == -1:
                break
            start = max(0, idx - 200)
            end = min(len(plain), idx + context_chars)
            hits.append(plain[start:end].strip())
            pos = idx + 1

    return "\n---\n".join(hits) if hits else ""
