"""Natural-language DM entry point for the Account Research Bot.

Replaces the legacy slash command. A DM containing an account name (with or
without conversational filler — "research Kroger", "look up Sysco", just
"Kroger") creates a session and posts the V1 persona-checkbox card. The
existing `run_research` action handler takes over from there.

Security:
- Raw user text is not logged. We hash it and log the parsed account name only
  (CLAUDE.md → input/log hygiene).
- The account name is escaped via safe_mrkdwn before it lands in any Slack
  block (handled inside build_persona_select_blocks).
"""
from __future__ import annotations

import logging
import re
from typing import Any, Callable, Dict, Optional

from src.research.persona_blocks import build_persona_select_blocks
from src.research.runner import run_account_research
from src.research.sessions import create_session
from src.security.log_redact import redact_user_text

logger = logging.getLogger(__name__)

# Conversational prefixes we strip before treating the rest as an account name.
# Each pattern matches at start-of-string and consumes either a trailing space
# (when the user supplied an account name) or end-of-string (when the user
# typed only filler — we surface a usage hint in that case).
_PREFIX_PATTERNS = [
    r"^\s*(?:hey|hi|hello|yo)[\s,]+",
    r"^\s*(?:can|could|would|will)\s+you\s+(?:please\s+)?",
    r"^\s*please\s+",
    r"^\s*(?:run\s+)?(?:a\s+)?research(?:\s+on)?(?:\s+|$)",
    r"^\s*look\s+up(?:\s+|$)",
    r"^\s*tell\s+me\s+about(?:\s+|$)",
    r"^\s*pull\s+(?:up\s+|some\s+)?(?:research\s+(?:on\s+)?)?",
    r"^\s*find\s+(?:me\s+)?(?:info\s+(?:on\s+)?)?",
    r"^\s*who\s+is(?:\s+|$)",
    r"^\s*what\s+(?:do\s+you\s+know\s+about|about)(?:\s+|$)",
]


def _extract_account_name(text: str) -> str:
    """Strip conversational filler and return the residual as the account name.

    Idempotent — if the user just types `Kroger`, returns `Kroger` unchanged.
    Trailing punctuation (`?`, `.`, `!`) is dropped. Empty result means no
    account name could be extracted.
    """
    if not text:
        return ""

    cleaned = text.strip()
    # Apply prefix strippers iteratively until nothing more matches.
    for _ in range(5):  # bounded loop — five passes is more than enough
        before = cleaned
        for pat in _PREFIX_PATTERNS:
            cleaned = re.sub(pat, "", cleaned, flags=re.IGNORECASE)
        if cleaned == before:
            break

    # Drop trailing punctuation and surrounding quotes.
    cleaned = cleaned.strip().strip('"\'').rstrip(".?!").strip()
    return cleaned


def handle_research_dm(
    message: Dict[str, Any],
    say: Callable[..., Any],
    ack: Optional[Callable[..., Any]] = None,
) -> None:
    """Bolt @app.message() handler. Treats every non-bot DM as a research request.

    `ack` is included for symmetry with action handlers; @app.message() doesn't
    require it but passing it through keeps the call-shape consistent for tests.
    """
    if ack is not None:
        try:
            ack()
        except Exception:  # pragma: no cover — defensive
            pass

    # Skip bot messages and edits — they cause loops or stale re-processing.
    if message.get("bot_id") or message.get("subtype"):
        return

    raw_text = (message.get("text") or "").strip()
    user_id = message.get("user") or ""

    # `clear` is a Slack-thread cleanup keyword — leave it alone for the
    # legacy clear handler to pick up.
    if raw_text.lower() == "clear":
        return

    if not raw_text:
        return

    account_name = _extract_account_name(raw_text)

    logger.info(
        "[dm_research] rep=%s text_hash=%s parsed_account=%r",
        user_id,
        redact_user_text(raw_text),
        account_name,
    )

    if not account_name:
        say(
            text=(
                "Send me an account name and I'll research it. "
                "Examples: `Kroger`, `research Sysco Foods`, `look up Pepsi`."
            ),
        )
        return

    session = create_session(rep_id=user_id, account_name=account_name)

    # Stage 0 — immediate placeholder so the DM doesn't look dead while
    # Exa + OpenRouter run (~10-15s).
    say(text=f":mag: Researching *{account_name}*…")

    # Stage 1 — account research (findings + HubSpot snapshot).
    run_account_research(session, say)

    # Stage 2 prep — persona-checkbox card. Click triggers run_persona_research.
    say(
        blocks=build_persona_select_blocks(
            account_name=account_name,
            session_id=session.session_id,
        ),
        text=f"Pick personas for {account_name}",
    )
