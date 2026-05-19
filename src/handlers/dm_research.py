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

from src.handlers.diff_front_door import should_show_diff_front_door
from src.handlers.followup_qa import (
    find_any_session_for_thread,
    get_bot_user_id,
    handle_followup,
    is_bot_mentioned,
    log_forbidden_followup,
)
from src.handlers.intent_capture import (
    intent_capture_card,
    is_account_ambiguous,
)
from src.integrations.slack_blocks import diff_front_door_card
from src.research.persona_blocks import build_persona_select_blocks
from src.research.runner import run_account_research
from src.research.sessions import create_session, get_session_by_thread_ts
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

    # ------------------------------------------------------------------
    # Thread-reply branch (spec §5 Move 2). If this message is a reply
    # inside an existing research thread, route to Q&A (when the bot is
    # `@`-mentioned) or silently ignore. We never fall through to the
    # new-research path from a thread reply — that would create rogue
    # sessions every time the rep replied to themselves.
    # ------------------------------------------------------------------
    thread_ts = message.get("thread_ts")
    if thread_ts:
        session_row = get_session_by_thread_ts(thread_ts, user_id)
        if session_row is not None:
            bot_user_id = get_bot_user_id(client) if client is not None else None
            if bot_user_id and is_bot_mentioned(raw_text, bot_user_id):
                handle_followup(message=message, say=say, client=client)
            return
        # No session matched for THIS rep. If a session exists for the
        # thread under a different rep AND the bot was mentioned, log a
        # `forbidden_followup` event (CLAUDE.md authz audit trail) — but
        # never post anything to Slack.
        bot_user_id = get_bot_user_id(client) if client is not None else None
        if bot_user_id and is_bot_mentioned(raw_text, bot_user_id):
            other = find_any_session_for_thread(thread_ts)
            if other is not None and getattr(other, "rep_id", None) != user_id:
                log_forbidden_followup(other.id, user_id)
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

    # Persist a DB Session row keyed by the in-memory session_id so the
    # thread-based Q&A path (spec docs/v1-daily-use-spec.md §5 Move 2) can
    # match a future thread reply back to this session via
    # `get_session_by_thread_ts(thread_ts, rep_id)`. Best-effort: if the
    # DB is unavailable we still let the research run proceed.
    try:
        from src.db.models import Session as DBSession
        from src.db.session import SessionLocal

        if SessionLocal is not None:
            db = SessionLocal()
            try:
                db_session = DBSession(
                    id=session.session_id,
                    account_name=account_name,
                    rep_id=user_id,
                )
                db_session.thread_ts = thread_ts
                db_session.channel_id = channel
                db.add(db_session)
                db.commit()
            finally:
                db.close()
    except Exception as e:  # noqa: BLE001
        # Narrow log — type only, never str(e) (CLAUDE.md log hygiene).
        logger.warning(
            "[dm_research] failed to persist Session row: %s",
            type(e).__name__,
        )

    def threaded_say(**kwargs: Any) -> Any:
        kwargs.setdefault("thread_ts", thread_ts)
        return say(**kwargs)

    # ------------------------------------------------------------------
    # Diff-first cold-start (spec §5 Move 4). If we've researched this
    # account within `SNAPSHOT_FRESHNESS_DAYS`, post the diff front-door
    # card and short-circuit — do NOT run research or post the persona
    # card. The rep picks `Re-research`, `Dig into what's new`, or
    # `Ask a specific question` from the card.
    # ------------------------------------------------------------------
    fresh_snapshot = should_show_diff_front_door(account_name)
    if fresh_snapshot is not None:
        # Persist a link from the new Session to the prior CompanyResearch
        # so `dig_into_new` can render the prior brief without rerunning
        # any external APIs (spec §5 Move 4 — `prior_company_research_id`).
        try:
            from src.db.models import CompanyResearch, Session as DBSession
            from src.db.session import SessionLocal
            from sqlalchemy.orm.attributes import flag_modified

            if SessionLocal is not None:
                db = SessionLocal()
                try:
                    prior_cr = (
                        db.query(CompanyResearch)
                        .filter(CompanyResearch.account_name.ilike(account_name))
                        .order_by(CompanyResearch.created_at.desc())
                        .first()
                    )
                    if prior_cr is not None:
                        db_session = (
                            db.query(DBSession)
                            .filter(DBSession.id == session.session_id)
                            .first()
                        )
                        if db_session is not None:
                            normalized = dict(db_session.normalized_request or {})
                            normalized["prior_company_research_id"] = prior_cr.id
                            db_session.normalized_request = normalized
                            flag_modified(db_session, "normalized_request")
                            db.commit()
                finally:
                    db.close()
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[dm_research] failed to link prior_company_research_id: %s",
                type(e).__name__,
            )

        threaded_say(
            blocks=diff_front_door_card(
                fresh_snapshot, account_name, session.session_id
            ),
            text=f"I have prior research on {account_name}.",
        )
        return

    # ------------------------------------------------------------------
    # Intent capture (spec §5 Move 1). No fresh snapshot → post the
    # intent-capture card and stop. Research does NOT start here — it
    # fires from the `intent_type` action handler once the rep clicks
    # one of the four intent buttons.
    # ------------------------------------------------------------------
    disambig_options = is_account_ambiguous(account_name)
    threaded_say(
        blocks=intent_capture_card(
            account_name=account_name,
            disambiguation_options=disambig_options,
            session_id=session.session_id,
        ),
        text=f"Quick check before I research {account_name}.",
    )
