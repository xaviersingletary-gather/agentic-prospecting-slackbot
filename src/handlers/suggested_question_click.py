"""Phase 6 (V1 Daily-Use spec §5 Move 3) — suggested-question click handler.

Lives in its own module so Phase 5's `followup_qa.py` is untouched.

Flow on a Slack `suggested_question` action:
    1. Ack within 3s (CLAUDE.md).
    2. Pull the question text, rep, channel, and thread_ts from the body.
    3. Log a `WorkflowEvent(event_type="suggested_question_clicked")` row
       with the question text so we can measure click-through.
    4. Synthesize a message dict shaped like the typed `@`-mention path.
    5. Delegate to `handle_followup` (Phase 5) — the click inherits authz,
       rate-limit, safe_mrkdwn, and latency logging behavior unchanged.

Security:
- Narrow exception handling at the outer boundary; one bad click never
  crashes the Bolt worker.
- No secrets logged. Exception type-names only via `safe_log_exception`.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, Optional

from src.db.models import Session as DBSession
from src.db.session import SessionLocal
from src.handlers.followup_qa import (
    _log_workflow_event,
    get_bot_user_id,
    handle_followup,
)
from src.security.exception_logger import safe_log_exception

logger = logging.getLogger(__name__)


def _lookup_session_id_for_thread(thread_ts: str) -> Optional[str]:
    """Return the most recent non-cancelled session_id for ``thread_ts``."""
    if not thread_ts or SessionLocal is None:
        return None
    db = SessionLocal()
    try:
        row = (
            db.query(DBSession)
            .filter(DBSession.thread_ts == thread_ts)
            .filter(DBSession.status != "cancelled")
            .order_by(DBSession.created_at.desc())
            .first()
        )
        return getattr(row, "id", None) if row else None
    except Exception as e:  # noqa: BLE001
        safe_log_exception(
            logger, e, "[suggested_question_click] session lookup failed"
        )
        return None
    finally:
        db.close()


def handle_suggested_question(
    ack: Callable[..., Any],
    body: Dict[str, Any],
    client: Any,
) -> None:
    """Route a `suggested_question` Slack button click through Q&A."""
    try:
        ack()
    except Exception as e:  # noqa: BLE001
        safe_log_exception(logger, e, "[suggested_question_click] ack failed")

    try:
        question = (body["actions"][0].get("value") or "").strip()
        user_id = body["user"]["id"]
        channel_id = body["channel"]["id"]
        message_block = body.get("message") or {}
        thread_ts = (
            message_block.get("thread_ts")
            or message_block.get("ts")
            or ""
        )
    except (KeyError, IndexError, TypeError) as e:
        safe_log_exception(
            logger, e, "[suggested_question_click] malformed body"
        )
        return

    if not question or not thread_ts:
        return

    session_id = _lookup_session_id_for_thread(thread_ts)

    # Log the click. We attach the question text so the
    # `suggested_question_clicked` payload is queryable for click-through
    # analytics. NOT the same as `followup_question` (which strips text).
    if SessionLocal is not None:
        db = SessionLocal()
        try:
            _log_workflow_event(
                db,
                event_type="suggested_question_clicked",
                session_id=session_id,
                rep_id=user_id,
                payload={"question_text": question},
            )
        finally:
            db.close()

    bot_user_id = get_bot_user_id(client) or ""
    mention_token = f"<@{bot_user_id}> " if bot_user_id else ""
    synthesized: Dict[str, Any] = {
        "text": f"{mention_token}{question}",
        "user": user_id,
        "channel": channel_id,
        "thread_ts": thread_ts,
        "ts": str(time.time()),
    }

    def _say(**kwargs: Any) -> Any:
        kwargs.setdefault("channel", channel_id)
        kwargs.setdefault("thread_ts", thread_ts)
        try:
            return client.chat_postMessage(**kwargs)
        except Exception as e:  # noqa: BLE001
            safe_log_exception(
                logger, e, "[suggested_question_click] synthesized say failed"
            )
            return None

    try:
        handle_followup(message=synthesized, say=_say, client=client)
    except Exception as e:  # noqa: BLE001
        safe_log_exception(
            logger,
            e,
            "[suggested_question_click] handle_followup raised",
        )
