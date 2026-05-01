"""Slack Block Kit renderer for tagged contacts (Phase 13).

Input shape (from `tag_contacts` / `build_tagged_contacts`):
    {"contacts": [...], "warning": Optional[str]}

Output:
    Block Kit blocks list. Existing-in-HubSpot group rendered first
    (per spec §1.2.1), net-new group second. Capped at 20 contacts
    (10 existing + 10 net-new); excess yields a "... and N more" line.

Every external string is run through `safe_mrkdwn` via the existing
`render_contact_for_slack` primitive (gate S1.2.1b).
"""
from __future__ import annotations

from typing import Any, Dict, List

from src.integrations.hubspot.contact_check import render_contact_for_slack
from src.security.safe_mrkdwn import safe_mrkdwn

EXISTING_CAP = 10
NET_NEW_CAP = 10


def build_contact_blocks(tag_result: Dict[str, Any]) -> List[Dict[str, Any]]:
    contacts = list(tag_result.get("contacts") or [])
    warning = tag_result.get("warning")

    existing = [c for c in contacts if c.get("status") == "EXISTS IN HUBSPOT"]
    net_new = [c for c in contacts if c.get("status") != "EXISTS IN HUBSPOT"]

    blocks: List[Dict[str, Any]] = []

    if warning:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"⚠️ {safe_mrkdwn(str(warning))}",
            },
        })

    header_text = (
        f"*👥 CONTACTS — {len(existing)} in HubSpot, {len(net_new)} net new*"
    )
    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": header_text},
    })

    # Existing first (spec §1.2.1)
    if existing:
        capped, extra = _cap(existing, EXISTING_CAP)
        lines = [render_contact_for_slack(c) for c in capped]
        if extra:
            lines.append(f"... and {extra} more")
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*EXISTS IN HUBSPOT*\n" + "\n".join(lines),
            },
        })

    # Net new second
    if net_new:
        capped, extra = _cap(net_new, NET_NEW_CAP)
        lines = [render_contact_for_slack(c) for c in capped]
        if extra:
            lines.append(f"... and {extra} more")
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*NET NEW*\n" + "\n".join(lines),
            },
        })

    if not existing and not net_new:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "_No contacts surfaced for the selected personas._",
            },
        })

    return blocks


def _cap(items: List[dict], cap: int):
    if len(items) <= cap:
        return items, 0
    return items[:cap], len(items) - cap
