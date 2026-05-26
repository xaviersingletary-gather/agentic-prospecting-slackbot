"""ICP sanity-check gate (May 26 V1 spec §7 New-Query Flow step 2).

Between disambiguation and intent capture, if an account is wildly outside
Gather AI's ICP (sub-$100M revenue, no warehouse footprint, not in target
verticals), surface a one-question confirm before burning agent cycles.
The rep can always override and proceed.

Trust posture mirrors `intent_capture.is_account_ambiguous`:
  - Read-only LLM call. No tools wired.
  - Failure → return None (treat as "looks in-ICP, skip the gate").
    Better to over-research than to block legitimate accounts.
  - Cached per lowercased account name; cost guard.
  - Exception type only in logs (CLAUDE.md log hygiene).
"""
from __future__ import annotations

import json
import logging
import re as _re
from typing import Any, Dict, List, Optional

import httpx

from src.config import settings
from src.security.safe_mrkdwn import safe_mrkdwn

logger = logging.getLogger(__name__)


# Per-name cache. `None` value means "we already checked and it looked
# in-ICP" — short-circuit the re-call.
_ICP_CACHE: Dict[str, Optional[Dict[str, str]]] = {}


_ICP_PROMPT = """You are a B2B sales qualification assistant for Gather AI,
a warehouse drone inventory automation company.

Gather AI's Ideal Customer Profile (ICP):
  - Revenue $500M+ (hard floor: ~$100M; below that is almost never a fit)
  - 10+ distribution or fulfillment centers in North America
  - Industries: Manufacturing, Food & Bev, Healthcare/Pharma, Retail
    (eCommerce, automotive, apparel), 3PL/Logistics
  - Owned or 3PL-managed warehouse network operations

Out-of-ICP red flags (any ONE is grounds to flag):
  - Sub-$100M revenue
  - No warehouse / distribution / fulfillment footprint
  - Pure software / SaaS / services with no physical operations
  - Consumer brand with no owned or managed warehousing
  - EMEA-only operations (Gather AI is US-only currently)
  - Fewer than 10 facilities even if in-vertical

Question: Based ONLY on what you know about the company "{account_name}",
does it look notably OUT of Gather AI's ICP?

Return ONE of two JSON shapes — no prose, no fences:

  If clearly out of ICP:
    {{"out_of_icp": true, "reason": "<one short sentence — e.g. 'pure SaaS, no warehouse operations'>"}}

  If in ICP or you cannot confidently say it is out:
    {{"out_of_icp": false}}

Bias toward `false` when unsure — false positives waste the rep's time.
"""


def _reset_cache_for_tests() -> None:
    """Test helper — clears the module-level cache between tests."""
    _ICP_CACHE.clear()


def check_icp_fit(account_name: str) -> Optional[Dict[str, str]]:
    """Return `{"reason": "..."}` if the account looks out-of-ICP, else None.

    Caller treats None as "skip the gate, proceed to intent capture".
    """
    if not account_name:
        return None

    key = account_name.lower().strip()
    if key in _ICP_CACHE:
        return _ICP_CACHE[key]

    if not settings.OPENROUTER_API_KEY:
        _ICP_CACHE[key] = None
        return None

    prompt = _ICP_PROMPT.format(account_name=account_name)

    try:
        response = httpx.post(
            f"{settings.OPENROUTER_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.OPENROUTER_MODEL,
                "max_tokens": 200,
                "temperature": 0.0,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=10,
        )
        response.raise_for_status()
        raw_text = (
            response.json()["choices"][0]["message"]["content"] or ""
        ).strip()
    except Exception as e:  # noqa: BLE001 — narrow log per CLAUDE.md
        logger.warning(
            "[icp_gate] ICP check failed: %s", type(e).__name__
        )
        _ICP_CACHE[key] = None
        return None

    parsed = _parse_icp_response(raw_text)
    _ICP_CACHE[key] = parsed
    return parsed


def _parse_icp_response(raw_text: str) -> Optional[Dict[str, str]]:
    """Coerce the LLM response into a `{reason}` dict or None.

    Tolerates fenced ```json blocks. Returns None on any parse failure,
    on `out_of_icp: false`, or when the reason is missing/empty.
    """
    if not raw_text:
        return None

    candidate = raw_text.strip()
    m = _re.match(r"^```(?:json)?\s*(.*?)\s*```\s*$", candidate, _re.DOTALL)
    if m:
        candidate = m.group(1).strip()

    try:
        value = json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        return None

    if not isinstance(value, dict):
        return None
    if not value.get("out_of_icp"):
        return None
    reason = str(value.get("reason") or "").strip()
    if not reason:
        return None
    # Cap to a reasonable length — the LLM occasionally rambles.
    return {"reason": reason[:240]}


def icp_override_card(
    *,
    account_name: str,
    reason: str,
    session_id: str,
) -> List[Dict[str, Any]]:
    """Render the override card the rep sees when the gate fires.

    Two buttons: "Research anyway" (proceeds to intent capture) and
    "Never mind" (drops the session). Both interpolated strings are
    sanitized through `safe_mrkdwn` (CLAUDE.md Slack output safety).
    """
    account_safe = safe_mrkdwn(account_name)
    reason_safe = safe_mrkdwn(reason)
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f":warning: *{account_safe}* looks outside Gather "
                    f"AI's ICP — _{reason_safe}_."
                ),
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "Want me to research it anyway? If yes I'll proceed "
                    "to intent capture."
                ),
            },
        },
        {
            "type": "actions",
            "block_id": f"icp_override_{session_id}",
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text", "text": "Research anyway"
                    },
                    "style": "primary",
                    "action_id": "icp_override_proceed",
                    "value": f"{session_id}::proceed",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Never mind"},
                    "action_id": "icp_override_cancel",
                    "value": f"{session_id}::cancel",
                },
            ],
        },
    ]
