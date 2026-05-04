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
from urllib.parse import urlparse

from src.security.safe_mrkdwn import safe_mrkdwn
from src.utils.citation_validator import (
    UNVERIFIED_PREFIX,
    is_unsourced_dc_count,
)

NO_DATA = "No public data found"
DC_NO_DATA = "Could not confirm DC count from public sources"
DIVIDER = "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
MAX_CLAIMS_PER_SECTION = 6

_FACT_SECTIONS = [
    ("📌 TRIGGER EVENTS", "trigger_events"),
    ("🏭 COMPETITOR SIGNALS", "competitor_signals"),
    ("📦 DISTRIBUTION / FACILITY INTEL", "dc_intel"),
    ("🎯 BOARD INITIATIVES", "board_initiatives"),
]


def _safe_url_link(url: str) -> str:
    """Render an http(s) URL as a Slack `<url|domain>` link.

    Falls back to a `safe_mrkdwn`-stripped bare URL if parsing fails or
    the scheme is non-http. Caller is responsible for ensuring the URL
    has already passed through `assert_safe_url` upstream — we don't
    re-validate here.
    """
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return safe_mrkdwn(url)
        domain = (parsed.netloc or "").replace("www.", "")
        if not domain:
            return safe_mrkdwn(url)
        # Strip the unsafe characters Slack uses for link syntax. The URL
        # is structurally constrained (post-SSRF guard), so this is
        # cosmetic — it removes any rare embedded `>|` from query
        # strings that would break the link.
        clean_url = url.replace(">", "").replace("|", "")
        clean_domain = domain.replace(">", "").replace("|", "")
        return f"<{clean_url}|{clean_domain}>"
    except Exception:  # noqa: BLE001
        return safe_mrkdwn(url)


def _render_fact_bullet(item: Dict[str, str], *, is_dc: bool) -> Optional[str]:
    """Return a rendered bullet, or None if the item must be dropped.

    DC intel items without a source URL are dropped (spec §1.4 — DC counts
    must never appear unsourced). Other unsourced facts are flagged with
    `⚠️ [Unverified]` instead of being dropped.
    """
    claim = safe_mrkdwn(item.get("claim", ""))
    url_raw = (item.get("source_url") or "").strip()
    if claim and url_raw:
        return f"•  {claim}  ·  {_safe_url_link(url_raw)}"
    if not claim:
        return None
    # Unsourced claim from here on
    if is_dc and is_unsourced_dc_count(claim):
        return None
    if is_dc:
        # Even non-DC-count items inside the DC section are risky; drop unsourced.
        return None
    return f"•  {UNVERIFIED_PREFIX} — {claim}"


def _render_fact_section(header: str, key: str, findings: Dict[str, Any]) -> str:
    items: List[Dict[str, str]] = findings.get(key) or []
    is_dc = key == "dc_intel"
    rendered: List[str] = []
    for item in items:
        bullet = _render_fact_bullet(item, is_dc=is_dc)
        if bullet is not None:
            rendered.append(bullet)

    overflow = 0
    if len(rendered) > MAX_CLAIMS_PER_SECTION:
        overflow = len(rendered) - MAX_CLAIMS_PER_SECTION
        rendered = rendered[:MAX_CLAIMS_PER_SECTION]

    lines = [f"*{header}*"]
    if rendered:
        lines.extend(rendered)
        if overflow:
            lines.append(f"_…and {overflow} more_")
    else:
        lines.append("•  _" + (DC_NO_DATA if is_dc else NO_DATA) + "_")
    return "\n".join(lines)


def _render_gaps_section(findings: Dict[str, Any]) -> str:
    gaps = findings.get("research_gaps") or []
    lines = ["*🔍 RESEARCH GAPS*"]
    if gaps:
        for gap in gaps:
            lines.append(f"•  {safe_mrkdwn(gap)}")
    else:
        lines.append(f"•  _{NO_DATA}_")
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
    """One header block + one section block per spec section, with
    dividers between sections so the eye can find each topic fast.

    Splitting by section keeps each text field well under Slack's 3000-char
    mrkdwn limit even on accounts with long lists of findings.
    """
    account = safe_mrkdwn(findings.get("account_name", "Unknown"))
    blocks: List[Dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"🏢 {account}", "emoji": True},
        },
    ]
    for header, key in _FACT_SECTIONS:
        blocks.append({"type": "divider"})
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": _render_fact_section(header, key, findings),
                },
            }
        )
    blocks.append({"type": "divider"})
    blocks.append(
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": _render_gaps_section(findings)},
        }
    )
    return blocks
