"""Strip Slack mrkdwn metacharacters from untrusted strings.

Apollo, Exa, HubSpot, Salesforce, and any LLM-rewritten text may contain
attacker-controlled `<https://attacker.com|click>` payloads that Slack
renders as clickable links. Every external string interpolated into a
Block Kit `mrkdwn` field must pass through this helper first.

See `CLAUDE.md` → Slack output safety, spec gate S1.2.
"""
from typing import Optional

_DANGEROUS_CHARS = ("<", ">", "|", "&")


def safe_mrkdwn(text: Optional[str]) -> str:
    if not text:
        return ""
    out = text
    for ch in _DANGEROUS_CHARS:
        out = out.replace(ch, "")
    return out
