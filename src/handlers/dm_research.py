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
    client: Any = None,
    ack: Optional[Callable[..., Any]] = None,
) -> None:
    """Bolt @app.message() handler. Treats every non-bot DM as a research request.

    `client` is the Bolt-provided Slack WebClient — used to update the
    progress status message inline via `chat.update`. When absent (test
    path), progress updates degrade silently to no-ops.

    `ack` is included for symmetry with action handlers; @app.message()
    doesn't require it but keeps the call-shape consistent.
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

    # Thread everything off the user's DM. `message.ts` is the original
    # message timestamp; all bot replies use it as `thread_ts` so they
    # collapse under the user's prompt instead of cluttering the channel.
    thread_ts = message.get("ts")
    channel = message.get("channel")

    def threaded_say(**kwargs: Any) -> Any:
        kwargs.setdefault("thread_ts", thread_ts)
        return say(**kwargs)

    # Stage 0 — live status message in the thread. `update_status`
    # rewrites this same message at each pipeline stage (Searching X →
    # Synthesizing → Done). If chat.update fails we just stop updating
    # — the static placeholder is still better than nothing.
    status_text = f":mag: *Researching {account_name}…*\n_Starting up_"
    status_resp = threaded_say(text=status_text)
    status_ts = (status_resp or {}).get("ts") if isinstance(status_resp, dict) else None
    if status_ts is None:
        # SlackResponse object with attribute access
        status_ts = getattr(status_resp, "data", {}).get("ts") if status_resp else None

    def update_status(line: str) -> None:
        if client is None or status_ts is None or channel is None:
            return
        try:
            client.chat_update(
                channel=channel,
                ts=status_ts,
                text=f":mag: *Researching {account_name}…*\n_{line}_",
            )
        except Exception:  # noqa: BLE001 — progress is best-effort
            pass

    # Stage 1 — pure research (Exa + OpenRouter). HubSpot stays in Stage 2.
    run_account_research(session, threaded_say, on_progress=update_status)

    # Final state on the status message — stays visible in the thread as
    # a "✓ Done" marker so the rep can scan back to it later.
    if client is not None and status_ts is not None and channel is not None:
        try:
            client.chat_update(
                channel=channel,
                ts=status_ts,
                text=f":white_check_mark: *Research complete for {account_name}*",
            )
        except Exception:  # noqa: BLE001
            pass

    # Stage 2 prep — persona-checkbox card. Click triggers run_persona_research.
    threaded_say(
        blocks=build_persona_select_blocks(
            account_name=account_name,
            session_id=session.session_id,
        ),
        text=f"Pick personas for {account_name}",
    )
