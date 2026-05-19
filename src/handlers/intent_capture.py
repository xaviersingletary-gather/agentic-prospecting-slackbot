"""Intent capture before research (V1 Daily-Use spec §5 Move 1).

Top-level DM with a new account name → post an intent-capture card
before any research runs. Card has two pieces:

  1. Disambiguation (optional) — surfaced only when an LLM judges the
     account name ambiguous (e.g. "Volvo" → Volvo Group vs. Volvo Cars).
  2. Intent type — always shown. Four fixed buttons:
       - Outbound first-touch
       - Pre-call prep
       - Renewal
       - Just digging

Clicking an intent button persists the choice and fires
`run_account_research`. Clicking a disambig button only updates
selection state; research starts when the intent button is clicked.

Security:
- All interpolated strings (including LLM-returned disambig labels)
  pass through `safe_mrkdwn` (CLAUDE.md §Slack output safety).
- LLM exceptions are logged by `type(e).__name__` only — never str(e)
  (CLAUDE.md §Input → log hygiene).
- The ambiguity check is read-only. Even if a prompt-injected
  account name tries to coerce the LLM into producing tool calls,
  this code path has no tools wired.
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


# Module-level cache. Keyed by `account_name.lower().strip()`. Stores
# either a list[dict] of disambiguation options or `None` (unambiguous).
# Intentional: a None entry is a real cached result and MUST short-
# circuit a second call so we never re-bill the LLM for the same name.
_AMBIGUITY_CACHE: Dict[str, Optional[List[Dict[str, str]]]] = {}


_AMBIGUITY_PROMPT = """You are a B2B sales research assistant.

Is the company name "{account_name}" ambiguous — i.e., are there multiple
notable real-world companies that share this name?

If YES (ambiguous): return a JSON array of 2-4 objects, each with:
  - "label": the disambiguated company name (e.g. "Volvo Group")
  - "distinguisher": a one-sentence description that separates it from
                     the others (industry, ownership, geography)

If NO (only one notable company matches the name): return the JSON
literal `null`.

Return ONLY the JSON value — no prose, no markdown fences.
"""


def _reset_cache_for_tests() -> None:
    """Test helper — clears the module-level cache between tests."""
    _AMBIGUITY_CACHE.clear()


def is_account_ambiguous(account_name: str) -> Optional[List[Dict[str, str]]]:
    """One-shot LLM check: does this account name refer to multiple
    notable companies?

    Returns:
        - A list of `{label, distinguisher}` dicts if ambiguous.
        - `None` if unambiguous or on any failure (LLM down, parse error,
          timeout). The caller treats `None` as "skip the disambig block,
          show intent buttons only" — never blocks the research flow.

    Cached per lowercased account name. A repeat call with the same key
    MUST NOT hit the LLM (cost guard — spec §5 Move 1).
    """
    if not account_name:
        return None

    key = account_name.lower().strip()
    if key in _AMBIGUITY_CACHE:
        return _AMBIGUITY_CACHE[key]

    if not settings.OPENROUTER_API_KEY:
        _AMBIGUITY_CACHE[key] = None
        return None

    prompt = _AMBIGUITY_PROMPT.format(account_name=account_name)

    try:
        response = httpx.post(
            f"{settings.OPENROUTER_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.OPENROUTER_MODEL,
                "max_tokens": 400,
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
            "[intent_capture] ambiguity check failed: %s",
            type(e).__name__,
        )
        _AMBIGUITY_CACHE[key] = None
        return None

    parsed = _parse_ambiguity_response(raw_text)
    _AMBIGUITY_CACHE[key] = parsed
    return parsed


def _parse_ambiguity_response(
    raw_text: str,
) -> Optional[List[Dict[str, str]]]:
    """Coerce the LLM response into either a sanitized list or None.

    Tolerates fenced ```json blocks. Returns None on any parse failure or
    if the response indicates no ambiguity.
    """
    if not raw_text:
        return None

    # Strip markdown fences.
    candidate = raw_text.strip()
    m = _re.match(r"^```(?:json)?\s*(.*?)\s*```\s*$", candidate, _re.DOTALL)
    if m:
        candidate = m.group(1).strip()

    # Bare "null"
    if candidate.lower() == "null":
        return None

    try:
        value = json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        return None

    if value is None:
        return None
    if not isinstance(value, list):
        return None

    cleaned: List[Dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        distinguisher = str(item.get("distinguisher") or "").strip()
        if not label:
            continue
        cleaned.append({"label": label, "distinguisher": distinguisher})

    if not cleaned:
        return None
    # Cap at 4 per spec.
    return cleaned[:4]


# ---------------------------------------------------------------------------
# Card builder
# ---------------------------------------------------------------------------

# Fixed intent options — value suffix maps to the persisted `intent_type`.
_INTENT_OPTIONS = [
    ("Outbound first-touch", "outbound"),
    ("Pre-call prep", "pre_call"),
    ("Renewal", "renewal"),
    ("Just digging", "just_digging"),
]


def intent_capture_card(
    account_name: str,
    disambiguation_options: Optional[List[Dict[str, str]]],
    session_id: str,
) -> List[Dict[str, Any]]:
    """Render the intent-capture Block Kit card.

    Always includes the four intent buttons. Includes a disambiguation
    block iff `disambiguation_options` is non-empty.

    Every interpolated string goes through `safe_mrkdwn` so attacker-
    controlled label/distinguisher payloads (poisoned LLM output) can't
    plant phishing-style mrkdwn links.
    """
    account_safe = safe_mrkdwn(account_name)
    blocks: List[Dict[str, Any]] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"Quick check before I research *{account_safe}*."
                ),
            },
        },
    ]

    if disambiguation_options:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Which {account_safe}?*",
            },
        })
        for opt in disambiguation_options:
            label_safe = safe_mrkdwn(opt.get("label") or "")
            distinguisher_safe = safe_mrkdwn(opt.get("distinguisher") or "")
            if distinguisher_safe:
                section_text = f"*{label_safe}* — {distinguisher_safe}"
            else:
                section_text = f"*{label_safe}*"
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": section_text},
                "accessory": {
                    "type": "button",
                    "text": {"type": "plain_text", "text": label_safe[:75] or "Pick"},
                    "action_id": "intent_disambig",
                    "value": f"{session_id}::{label_safe}",
                },
            })

    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": "*Why are you looking?*"},
    })

    intent_buttons: List[Dict[str, Any]] = []
    for label, value_suffix in _INTENT_OPTIONS:
        intent_buttons.append({
            "type": "button",
            "text": {"type": "plain_text", "text": label},
            "action_id": "intent_type",
            "value": f"{session_id}::{value_suffix}",
        })

    blocks.append({
        "type": "actions",
        "block_id": f"intent_capture_{session_id}",
        "elements": intent_buttons,
    })

    return blocks
