"""Render the spec §1.2 5-section research dump.

Input shape (`findings`):
    {
        "account_name": str,
        "trigger_events":     [{"claim": str, "source_url": str}, ...],
        "competitor_signals": [{"claim": str, "source_url": str}, ...],
        "dc_intel":           [{"claim": str, "source_url": str}, ...],   # may be []
        "board_initiatives":  [{"claim": str, "source_url": str}, ...],
        "research_gaps":      [str, ...],
    }

Output:
    - `format_research_output(findings)` returns plain mrkdwn text
    - `build_research_blocks(findings)` returns a Block Kit blocks list

Every interpolated string is run through `safe_mrkdwn` (gate S1.2).
Empty sections render with "No public data found" except the DC section,
which uses the spec-mandated "Could not confirm DC count from public sources".
No outreach / messaging content is ever produced — that is a V2 feature.
"""
from typing import Any, Dict, List, Optional

from src.security.safe_mrkdwn import safe_mrkdwn
from src.utils.citation_validator import (
    UNVERIFIED_PREFIX,
    is_unsourced_dc_count,
)

NO_DATA = "No public data found"
DC_NO_DATA = "Could not confirm DC count from public sources"
DIVIDER = "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

_FACT_SECTIONS = [
    ("📌 TRIGGER EVENTS", "trigger_events"),
    ("🏭 COMPETITOR SIGNALS", "competitor_signals"),
    ("📦 DISTRIBUTION / FACILITY INTEL", "dc_intel"),
    ("🎯 BOARD INITIATIVES", "board_initiatives"),
]


def _render_fact_bullet(item: Dict[str, str], *, is_dc: bool) -> Optional[str]:
    """Return a rendered bullet, or None if the item must be dropped.

    DC intel items without a source URL are dropped (spec §1.4 — DC counts
    must never appear unsourced). Other unsourced facts are flagged with
    `⚠️ [Unverified]` instead of being dropped.
    """
    claim = safe_mrkdwn(item.get("claim", ""))
    url = safe_mrkdwn(item.get("source_url", ""))
    if claim and url:
        return f"• {claim} — {url}"
    if not claim:
        return None
    # Unsourced claim from here on
    if is_dc and is_unsourced_dc_count(claim):
        return None
    if is_dc:
        # Even non-DC-count items inside the DC section are risky; drop unsourced.
        return None
    return f"• {UNVERIFIED_PREFIX} — {claim}"


def _render_fact_section(header: str, key: str, findings: Dict[str, Any]) -> str:
    items: List[Dict[str, str]] = findings.get(key) or []
    is_dc = key == "dc_intel"
    rendered: List[str] = []
    for item in items:
        bullet = _render_fact_bullet(item, is_dc=is_dc)
        if bullet is not None:
            rendered.append(bullet)
    lines = [header]
    if rendered:
        lines.extend(rendered)
    else:
        lines.append("• " + (DC_NO_DATA if is_dc else NO_DATA))
    return "\n".join(lines)


def _render_gaps_section(findings: Dict[str, Any]) -> str:
    gaps = findings.get("research_gaps") or []
    lines = ["🔍 RESEARCH GAPS"]
    if gaps:
        for gap in gaps:
            lines.append(f"• {safe_mrkdwn(gap)}")
    else:
        lines.append(f"• {NO_DATA}")
    return "\n".join(lines)


def format_research_output(findings: Dict[str, Any]) -> str:
    account = safe_mrkdwn(findings.get("account_name", "Unknown"))
    sections = [f"🏢 {account}", DIVIDER, ""]
    for header, key in _FACT_SECTIONS:
        sections.append(_render_fact_section(header, key, findings))
        sections.append("")
    sections.append(_render_gaps_section(findings))
    return "\n".join(sections).rstrip() + "\n"


def build_research_blocks(findings: Dict[str, Any]) -> List[Dict[str, Any]]:
    """One header block + one section block per spec section.

    Splitting by section keeps each text field well under Slack's 3000-char
    mrkdwn limit even on accounts with long lists of findings.
    """
    account = safe_mrkdwn(findings.get("account_name", "Unknown"))
    blocks: List[Dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"🏢 {account}", "emoji": True},
        },
        {"type": "divider"},
    ]
    for header, key in _FACT_SECTIONS:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": _render_fact_section(header, key, findings),
                },
            }
        )
    blocks.append(
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": _render_gaps_section(findings)},
        }
    )
    return blocks
