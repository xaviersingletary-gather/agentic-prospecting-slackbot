"""Thread-keyed persistence for the May 26 V1 spec.

Helpers around the `account_research` and `conversation_turns` tables.
Coexists with the legacy Session / CompanyResearch path during the
transition; nothing here writes to or reads from the legacy tables.

Trust boundary: callers are expected to have already sanitized any
account_name / message content that lands in Slack output. This module
treats `research_blob` as opaque JSON and `message` as opaque text.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.orm.attributes import flag_modified

from src.db.models import AccountResearch, ConversationTurn
from src.db.session import SessionLocal
from src.security.exception_logger import safe_log_exception

logger = logging.getLogger(__name__)


def get_account_research_by_thread_ts(thread_ts: str) -> Optional[AccountResearch]:
    """Return the AccountResearch row for a thread, or None.

    Returns a detached copy (SQLAlchemy object expunged from the session)
    so callers can read attributes after the session closes.
    """
    if not thread_ts or SessionLocal is None:
        return None
    db = SessionLocal()
    try:
        row = (
            db.query(AccountResearch)
            .filter(AccountResearch.thread_ts == thread_ts)
            .first()
        )
        if row is not None:
            db.expunge(row)
        return row
    except Exception as e:  # noqa: BLE001
        safe_log_exception(logger, "get_account_research_by_thread_ts", e)
        return None
    finally:
        db.close()


def upsert_account_research(
    *,
    thread_ts: str,
    account_name: str,
    rep_id: Optional[str] = None,
    channel_id: Optional[str] = None,
    intent: Optional[str] = None,
    research_blob: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Insert or update the AccountResearch row keyed on thread_ts.

    Returns the row id on success, None on failure. Failure modes log the
    exception type but never raise — callers degrade gracefully.
    """
    if not thread_ts or not account_name or SessionLocal is None:
        return None
    db = SessionLocal()
    try:
        row = (
            db.query(AccountResearch)
            .filter(AccountResearch.thread_ts == thread_ts)
            .first()
        )
        if row is None:
            row = AccountResearch(
                thread_ts=thread_ts,
                account_name=account_name,
                rep_id=rep_id,
                channel_id=channel_id,
                intent=intent,
                research_blob=research_blob,
            )
            db.add(row)
        else:
            # Only overwrite fields the caller supplied. Preserves earlier
            # writes (e.g. intent captured before research_blob exists).
            if account_name is not None:
                row.account_name = account_name
            if rep_id is not None:
                row.rep_id = rep_id
            if channel_id is not None:
                row.channel_id = channel_id
            if intent is not None:
                row.intent = intent
            if research_blob is not None:
                row.research_blob = research_blob
                flag_modified(row, "research_blob")
        db.commit()
        return row.id
    except Exception as e:  # noqa: BLE001
        safe_log_exception(logger, "upsert_account_research", e)
        try:
            db.rollback()
        except Exception:  # pragma: no cover — defensive
            pass
        return None
    finally:
        db.close()


def append_conversation_turn(
    *,
    thread_ts: str,
    role: str,
    message: str,
    rep_id: Optional[str] = None,
) -> Optional[str]:
    """Append a user or assistant turn to the thread history.

    Returns the new row id on success, None on failure.
    """
    if role not in ("user", "assistant"):
        raise ValueError(f"role must be 'user' or 'assistant', got {role!r}")
    if not thread_ts or not message or SessionLocal is None:
        return None
    db = SessionLocal()
    try:
        row = ConversationTurn(
            thread_ts=thread_ts,
            role=role,
            message=message,
            rep_id=rep_id,
        )
        db.add(row)
        db.commit()
        return row.id
    except Exception as e:  # noqa: BLE001
        safe_log_exception(logger, "append_conversation_turn", e)
        try:
            db.rollback()
        except Exception:  # pragma: no cover — defensive
            pass
        return None
    finally:
        db.close()


def get_recent_turns(thread_ts: str, limit: int = 20) -> List[ConversationTurn]:
    """Return up to `limit` most-recent turns for a thread, oldest-first.

    The Follow-up Q&A path uses this to assemble the prompt history. The
    list is detached from the SQLAlchemy session so callers can read it
    after the session closes.
    """
    if not thread_ts or SessionLocal is None:
        return []
    db = SessionLocal()
    try:
        rows = (
            db.query(ConversationTurn)
            .filter(ConversationTurn.thread_ts == thread_ts)
            .order_by(ConversationTurn.created_at.desc())
            .limit(limit)
            .all()
        )
        for r in rows:
            db.expunge(r)
        # Oldest-first for downstream prompt assembly.
        rows.reverse()
        return rows
    except Exception as e:  # noqa: BLE001
        safe_log_exception(logger, "get_recent_turns", e)
        return []
    finally:
        db.close()


def has_research_for_thread(thread_ts: str) -> bool:
    """Cheap existence check used by the Slack handler to pick the route.

    `dm_research` calls this before `get_account_research_by_thread_ts`
    so the unhappy path (no row) doesn't pay the cost of materializing
    the row.
    """
    if not thread_ts or SessionLocal is None:
        return False
    db = SessionLocal()
    try:
        return (
            db.query(AccountResearch.id)
            .filter(AccountResearch.thread_ts == thread_ts)
            .first()
            is not None
        )
    except Exception as e:  # noqa: BLE001
        safe_log_exception(logger, "has_research_for_thread", e)
        return False
    finally:
        db.close()
