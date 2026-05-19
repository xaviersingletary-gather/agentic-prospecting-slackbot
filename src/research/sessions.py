"""In-memory research-session store (spec §1.3, project state V1).

Survives a single interaction callback but is lost on restart — acceptable
for V1. V2 moves this to Redis.

Also exposes a thin DB lookup helper (`get_session_by_thread_ts`) that the
thread-based Q&A path (spec docs/v1-daily-use-spec.md §5 Move 2) uses to
match a Slack thread reply to the persisted `Session` row.
"""
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ResearchSession:
    session_id: str
    rep_id: str
    account_name: str
    personas: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    # Cache of Stage 1 findings so Stage 2 (angle builder) can ground its
    # output without re-running Exa/OpenRouter. None until Stage 1 finishes.
    findings: Optional[Dict[str, Any]] = None
    # Captured intent + disambiguation from the V1 Daily-Use intent
    # capture card (spec §5 Move 1). Keys we use: `intent_type`,
    # `disambiguation`, `prior_company_research_id`. Lives on the
    # ResearchSession so `run_account_research` can thread the captured
    # intent into the agent prompts without re-reading the DB.
    normalized_request: Optional[Dict[str, Any]] = None


_SESSIONS: Dict[str, ResearchSession] = {}


def create_session(rep_id: str, account_name: str) -> ResearchSession:
    sid = uuid.uuid4().hex
    sess = ResearchSession(session_id=sid, rep_id=rep_id, account_name=account_name)
    _SESSIONS[sid] = sess
    return sess


def get_session(session_id: str) -> Optional[ResearchSession]:
    return _SESSIONS.get(session_id)


def update_personas(session_id: str, personas: List[str]) -> Optional[ResearchSession]:
    sess = _SESSIONS.get(session_id)
    if sess is None:
        return None
    sess.personas = list(personas)
    return sess


def set_findings(session_id: str, findings: Dict[str, Any]) -> None:
    sess = _SESSIONS.get(session_id)
    if sess is not None:
        sess.findings = findings


def _reset_for_tests() -> None:
    _SESSIONS.clear()


def get_session_by_thread_ts(thread_ts: str, rep_id: str) -> Optional[Any]:
    """Return the most recent non-cancelled DB `Session` row for the given
    Slack `thread_ts` + `rep_id`, or `None` if no match.

    "Most recent" = highest `created_at`. Used by the thread-based Q&A
    handler to verify (a) the thread maps to a real session and (b) the
    replying user owns it (spec §5 Move 2).

    Returns `None` (rather than raising) when the DB is unavailable or
    inputs are empty — callers treat `None` as "no match" and silently
    ignore the reply, which is the right authz default.
    """
    if not thread_ts or not rep_id:
        return None

    # Imported lazily so this module stays importable in environments
    # without `DATABASE_URL` (e.g. legacy tests of the in-memory store).
    try:
        from src.db.session import SessionLocal
        from src.db.models import Session as DBSession
    except Exception:  # noqa: BLE001 — DB layer optional in some test envs
        return None

    if SessionLocal is None:
        return None

    db = SessionLocal()
    try:
        return (
            db.query(DBSession)
            .filter(DBSession.thread_ts == thread_ts)
            .filter(DBSession.rep_id == rep_id)
            .filter(DBSession.status != "cancelled")
            .order_by(DBSession.created_at.desc())
            .first()
        )
    except Exception as e:  # noqa: BLE001
        # Narrow log: type only, never str(e) (may leak request bodies
        # from SQLAlchemy — CLAUDE.md input/log hygiene).
        logger.warning(
            "[sessions] get_session_by_thread_ts failed: %s",
            type(e).__name__,
        )
        return None
    finally:
        db.close()
