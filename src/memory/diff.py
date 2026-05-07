"""Diff two findings dicts to surface "what's new since last research."

Diff identity is ``source_url``. Claims are LLM-generated and rephrase
across runs even when they describe the same underlying fact, so claim
text is unsafe as a key. URLs survive the sanitize step in
``findings_builder._sanitize_findings`` and are SSRF-checked, so they
are stable per-fact.

``research_gaps`` is excluded from diffing — gaps are run-conditioned
metadata (persona scope, retry messages) and rarely meaningful as a
"new since" surface.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# Sections we diff. Mirrors `_FACT_KEYS` in findings_builder.py.
DIFFED_SECTIONS = (
    "trigger_events",
    "competitor_signals",
    "dc_intel",
    "board_initiatives",
)


def _section_urls(findings: Dict[str, Any], key: str) -> set[str]:
    items = findings.get(key) or []
    if not isinstance(items, list):
        return set()
    out: set[str] = set()
    for it in items:
        if not isinstance(it, dict):
            continue
        url = (it.get("source_url") or "").strip()
        if url:
            out.add(url)
    return out


def diff_findings(
    prev: Optional[Dict[str, Any]],
    new: Optional[Dict[str, Any]],
) -> Dict[str, List[Dict[str, str]]]:
    """Return per-section lists of items in ``new`` whose source_url is
    absent from ``prev``. Cold start (``prev`` is ``None`` or has no
    findings) returns an empty diff — the caller renders nothing.

    Output shape::

        {
            "trigger_events":     [{"claim": ..., "source_url": ...}, ...],
            "competitor_signals": [...],
            "dc_intel":           [...],
            "board_initiatives":  [...],
        }
    """
    if not isinstance(new, dict):
        return {k: [] for k in DIFFED_SECTIONS}
    if not isinstance(prev, dict):
        return {k: [] for k in DIFFED_SECTIONS}

    out: Dict[str, List[Dict[str, str]]] = {}
    for key in DIFFED_SECTIONS:
        prev_urls = _section_urls(prev, key)
        new_items: List[Dict[str, str]] = []
        for it in new.get(key) or []:
            if not isinstance(it, dict):
                continue
            url = (it.get("source_url") or "").strip()
            claim = (it.get("claim") or "").strip()
            if not url or not claim:
                continue
            if url in prev_urls:
                continue
            new_items.append({"claim": claim, "source_url": url})
        out[key] = new_items
    return out


def diff_is_empty(diff: Dict[str, List[Dict[str, str]]]) -> bool:
    """True iff no section has any new items."""
    return not any(diff.get(k) for k in DIFFED_SECTIONS)
