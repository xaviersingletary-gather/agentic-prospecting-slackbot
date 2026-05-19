"""Diff-first cold-start front door (V1 Daily-Use spec §5 Move 4).

When a rep DMs an account name we've already researched recently, we skip
the full pipeline and post a small re-entry card with three options:
  - `re_research`    — kick off a fresh research run.
  - `dig_into_new`   — open the prior brief (no Exa/Apollo/HubSpot calls).
  - `ask_specific`   — hint the rep to @-mention with a question.

Freshness is governed by `settings.SNAPSHOT_FRESHNESS_DAYS` (default 14).

Security:
- All snapshot strings are treated as untrusted (poisoned Exa pages can
  land in findings). Rendering goes through `safe_mrkdwn` in the card
  builder (see `slack_blocks.diff_front_door_card`).
- Exception logs use `safe_log_exception` — type name only, never
  stringified payloads (CLAUDE.md §Input → log hygiene).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from src.config import settings
from src.memory.snapshots import get_latest_snapshot
from src.security.exception_logger import safe_log_exception

logger = logging.getLogger(__name__)


def _parse_saved_at(value: str) -> Optional[datetime]:
    """Parse an ISO-8601 UTC timestamp produced by `save_snapshot`.

    Returns a tz-aware datetime in UTC, or None on any parse failure.
    """
    if not value or not isinstance(value, str):
        return None
    try:
        s = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def should_show_diff_front_door(account_name: str) -> Optional[Dict[str, Any]]:
    """Return the most-recent snapshot if it's fresh enough to short-circuit
    a full research run; else None.

    Fail-open: any IO / parse / config failure returns None so a broken
    snapshot can never block research (spec §6 — "Snapshot is 15 days old
    → Treated as cold").
    """
    if not account_name:
        return None

    try:
        snapshot = get_latest_snapshot(account_name)
    except Exception as e:  # noqa: BLE001
        safe_log_exception(logger, e, "[diff_front_door] get_latest_snapshot failed")
        return None

    if not snapshot or not isinstance(snapshot, dict):
        return None

    saved_at_raw = snapshot.get("saved_at")
    saved_at = _parse_saved_at(saved_at_raw) if isinstance(saved_at_raw, str) else None
    if saved_at is None:
        return None

    try:
        now = datetime.now(timezone.utc)
        age_days = (now - saved_at).total_seconds() / 86400.0
    except Exception as e:  # noqa: BLE001
        safe_log_exception(logger, e, "[diff_front_door] age computation failed")
        return None

    try:
        threshold = float(settings.SNAPSHOT_FRESHNESS_DAYS)
    except (TypeError, ValueError):
        threshold = 14.0

    if age_days <= threshold:
        return snapshot
    return None


def dig_into_new(
    session_id: str,
    channel_id: str,
    thread_ts: str,
    client: Any,
    say: Callable[..., Any],
) -> None:
    """Post the prior research brief into the thread — no fresh retrieval.

    Looks up the new Session row, reads `normalized_request["prior_company_research_id"]`,
    loads that `CompanyResearch`, and renders it via the existing
    `research_brief_card`. Never touches Exa/Apollo/HubSpot (spec §7
    Success Criterion 18).

    Soft-fails on every error: posts a fallback message rather than
    raising, since the rep is already in the thread waiting.
    """
    # Lazy imports keep this module importable in test envs without DB.
    try:
        from src.db.models import CompanyResearch, Session as DBSession
        from src.db.session import SessionLocal
        from src.integrations.slack_blocks import research_brief_card
    except Exception as e:  # noqa: BLE001
        safe_log_exception(logger, e, "[diff_front_door] dig_into_new import failed")
        try:
            say(
                text="I couldn't load the prior research. Try `Re-research` instead.",
                thread_ts=thread_ts,
            )
        except Exception as inner:  # noqa: BLE001
            safe_log_exception(logger, inner, "[diff_front_door] dig_into_new fallback post failed")
        return

    if SessionLocal is None:
        try:
            say(
                text="I couldn't load the prior research. Try `Re-research` instead.",
                thread_ts=thread_ts,
            )
        except Exception as e:  # noqa: BLE001
            safe_log_exception(logger, e, "[diff_front_door] dig_into_new no-DB say failed")
        return

    db = None
    try:
        db = SessionLocal()
        new_session = db.query(DBSession).filter(DBSession.id == session_id).first()
        if not new_session:
            say(
                text="Session not found. Send the account name again to start over.",
                thread_ts=thread_ts,
            )
            return

        normalized = new_session.normalized_request or {}
        prior_id = None
        if isinstance(normalized, dict):
            prior_id = normalized.get("prior_company_research_id")

        prior_cr = None
        if prior_id:
            prior_cr = (
                db.query(CompanyResearch)
                .filter(CompanyResearch.id == prior_id)
                .first()
            )

        # Fallback: locate any prior CompanyResearch by matching account name.
        if prior_cr is None:
            prior_cr = (
                db.query(CompanyResearch)
                .filter(CompanyResearch.account_name.ilike(new_session.account_name or ""))
                .order_by(CompanyResearch.created_at.desc())
                .first()
            )

        if prior_cr is None:
            say(
                text=(
                    "I don't have a saved research brief for this account yet — "
                    "try `Re-research` to run one now."
                ),
                thread_ts=thread_ts,
            )
            return

        research_dict = {
            "account_name": prior_cr.account_name,
            "facility_count": prior_cr.facility_count,
            "facility_count_note": prior_cr.facility_count_note,
            "board_initiatives": prior_cr.board_initiatives or [],
            "company_priorities": prior_cr.company_priorities or [],
            "trigger_events": prior_cr.trigger_events or [],
            "automation_vendors": prior_cr.automation_vendors or [],
            "exception_tax": prior_cr.exception_tax,
            "research_gaps": prior_cr.research_gaps or [],
            "documents_used": prior_cr.documents_used or [],
        }

        say(
            blocks=research_brief_card(research_dict, session_id),
            text=f"Prior research brief for {prior_cr.account_name}",
            thread_ts=thread_ts,
        )
    except Exception as e:  # noqa: BLE001
        safe_log_exception(logger, e, "[diff_front_door] dig_into_new failed")
        try:
            say(
                text="I couldn't load the prior research. Try `Re-research` instead.",
                thread_ts=thread_ts,
            )
        except Exception as inner:  # noqa: BLE001
            safe_log_exception(logger, inner, "[diff_front_door] dig_into_new fallback post failed")
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:  # noqa: BLE001
                pass


def ask_specific_hint(say: Callable[..., Any], thread_ts: Optional[str] = None) -> None:
    """Post a one-line hint pointing the rep at the @-mention Q&A path."""
    try:
        kwargs: Dict[str, Any] = {
            "text": (
                "`@`-mention me with your question and I'll answer "
                "from saved research."
            ),
        }
        if thread_ts:
            kwargs["thread_ts"] = thread_ts
        say(**kwargs)
    except Exception as e:  # noqa: BLE001
        safe_log_exception(logger, e, "[diff_front_door] ask_specific_hint say failed")
