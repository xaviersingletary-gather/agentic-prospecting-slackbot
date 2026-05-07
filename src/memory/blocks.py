"""Render the "🆕 New since [date]" Slack section.

Produces a small block stack that the research runner prepends to the
standard Block Kit output when a prior snapshot exists and the diff has
at least one new item. When the diff is empty this returns ``[]`` so
callers can splat unconditionally.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from src.memory.diff import DIFFED_SECTIONS, diff_is_empty
from src.security.safe_mrkdwn import safe_mrkdwn

_SECTION_LABELS = {
    "trigger_events": "📌 Trigger events",
    "competitor_signals": "🏭 Competitor signals",
    "dc_intel": "📦 DC / facility intel",
    "board_initiatives": "🎯 Board initiatives",
}

_MAX_NEW_PER_SECTION = 4


def _format_saved_at(saved_at: Optional[str]) -> str:
    """Render the prior snapshot's timestamp as a short date string.

    Falls back to the raw value (sanitized) if parsing fails. Always
    safe-mrkdwn'd before returning.
    """
    if not saved_at:
        return "earlier"
    try:
        dt = datetime.strptime(saved_at, "%Y-%m-%dT%H:%M:%SZ")
        return safe_mrkdwn(dt.strftime("%b %d, %Y"))
    except (TypeError, ValueError):
        return safe_mrkdwn(saved_at)


def _safe_link(url: str) -> str:
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return safe_mrkdwn(url)
        domain = (parsed.netloc or "").replace("www.", "")
        if not domain:
            return safe_mrkdwn(url)
        clean_url = url.replace(">", "").replace("|", "")
        clean_domain = domain.replace(">", "").replace("|", "")
        return f"<{clean_url}|{clean_domain}>"
    except Exception:  # noqa: BLE001
        return safe_mrkdwn(url)


def build_new_since_blocks(
    diff: Dict[str, List[Dict[str, str]]],
    prev_saved_at: Optional[str],
) -> List[Dict[str, Any]]:
    """Block Kit blocks for the "new since" section. Empty list if no diff."""
    if not isinstance(diff, dict) or diff_is_empty(diff):
        return []

    when = _format_saved_at(prev_saved_at)
    blocks: List[Dict[str, Any]] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"🆕 New since {when}",
                "emoji": True,
            },
        }
    ]

    for key in DIFFED_SECTIONS:
        items = diff.get(key) or []
        if not items:
            continue
        label = _SECTION_LABELS[key]
        lines: List[str] = [f"*{label}*"]
        overflow = max(0, len(items) - _MAX_NEW_PER_SECTION)
        for it in items[:_MAX_NEW_PER_SECTION]:
            claim = safe_mrkdwn(it.get("claim", ""))
            url = (it.get("source_url") or "").strip()
            if claim and url:
                lines.append(f"•  {claim}  ·  {_safe_link(url)}")
            elif claim:
                lines.append(f"•  {claim}")
        if overflow:
            lines.append(f"_…and {overflow} more_")
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "\n".join(lines)},
            }
        )

    blocks.append({"type": "divider"})
    return blocks
