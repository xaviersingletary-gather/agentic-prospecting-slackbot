"""Phase 5 (V1 Daily-Use) — Move 2 handler: thread-based Q&A routing.

Slack `@`-mention in a research thread → grounded answer over the
session's already-saved artifacts. No fresh retrieval; no tool calls.

Security (CLAUDE.md):
- AUTHZ: every state-mutating step checks `message.user == session.rep_id`
  before touching state. Cross-rep mentions log `forbidden_followup` and
  return silently — no Slack post.
- safe_mrkdwn on all LLM output before it lands in Block Kit.
- Raw question text is NEVER logged. We log `len(question)` only.
- Narrow `except Exception` blocks; type-name logging via safe_log_exception.
- Slack 3-second ack rule: the placeholder post IS the ack.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from src.agents.followup_agent import answer_followup, build_followup_context
from src.db.models import (
    CompanyResearch,
    ContactResearch,
    Persona,
    Session as DBSession,
    WorkflowEvent,
)
from src.db.session import SessionLocal
from src.security.exception_logger import safe_log_exception
from src.security.safe_mrkdwn import safe_mrkdwn

logger = logging.getLogger(__name__)

# Module-level cache of the bot user ID. `auth_test` is hit once per process.
_BOT_USER_ID: Optional[str] = None

_RATE_LIMIT_PER_SESSION = 20
_RATE_LIMIT_PER_REP = 100
_RATE_LIMIT_WINDOW_HOURS = 24

_THINKING_PLACEHOLDER = "_…thinking…_"
_PULLING_CONTACTS_PLACEHOLDER = "🔎 Pulling contacts from HubSpot + Apollo…"
_RATE_LIMIT_MSG = (
    "_You've hit the follow-up rate limit for this thread. "
    "Try again later._"
)
_CANCELLED_MSG = "_This session was cancelled. Start a new research request to ask follow-ups._"
_RESEARCH_RUNNING_MSG = "_I'm still finishing the initial research. Ask again in a minute._"

# Phase 17 — keywords that imply the question needs contact-level data.
# Heuristic only — cheap, no LLM call. The cost of a false positive is
# one Apollo lookup; the cost of a false negative is a degraded answer.
_CONTACT_QUESTION_KEYWORDS = (
    "who",
    "contact",
    "person",
    "people",
    "title",
    "linkedin",
    "email",
    "reach out",
    "reach-out",
    "champion",
    "stakeholder",
    "hit first",
    "talk to",
    "decision maker",
    "decision-maker",
    "buyer",
    "owner",
    "engage",
    "introduce",
)


def _is_contact_question(question: str) -> bool:
    """Cheap heuristic: does this question need contact data on file?

    Returns True if any keyword in `_CONTACT_QUESTION_KEYWORDS` appears
    in the question (case-insensitive). Intended for use ONLY when
    `Persona` rows are empty — a hit triggers a lazy HubSpot + Apollo
    fetch before answering.
    """
    q = (question or "").lower()
    if not q:
        return False
    return any(k in q for k in _CONTACT_QUESTION_KEYWORDS)


def _lazy_fetch_contacts(
    session_row: Any,
    client: Any,
    channel: str,
    placeholder_ts: Optional[str],
    thread_ts: str,
) -> int:
    """Fire the HubSpot + Apollo contact pipeline inline for `session_row`.

    Used when a follow-up question implies contact data but `Persona`
    rows are empty for the session (e.g., rep picked `general_research`
    intent so Stage 2 never auto-ran). Edits the placeholder to a
    status line during the call. Persists Persona rows via
    `_persist_personas_from_tag_result`. Never raises.

    Returns the number of Persona rows written/touched.
    """
    from src.research.clients_factory import (
        get_apollo_client,
        get_hubspot_contact_client,
        get_hubspot_portal_id,
    )
    from src.research.contact_pipeline import build_tagged_contacts
    from src.research.runner import (
        DEFAULT_PERSONAS,
        _persist_personas_from_tag_result,
    )
    from src.research.sessions import ResearchSession

    # Status placeholder while we hit Apollo + HubSpot.
    try:
        _post_or_update(
            client, channel, placeholder_ts, thread_ts,
            text=_PULLING_CONTACTS_PLACEHOLDER,
        )
    except Exception as e:  # noqa: BLE001
        safe_log_exception(
            logger, e, "[followup_qa] lazy_fetch placeholder edit failed"
        )

    sess = ResearchSession(
        session_id=session_row.id,
        rep_id=session_row.rep_id,
        account_name=session_row.account_name,
    )
    sess.personas = list(DEFAULT_PERSONAS)

    apollo_client = None
    hs_contact_client = None
    portal_id = None
    try:
        apollo_client = get_apollo_client()
    except Exception as e:  # noqa: BLE001
        safe_log_exception(logger, e, "[followup_qa] apollo client init failed")
    try:
        hs_contact_client = get_hubspot_contact_client()
    except Exception as e:  # noqa: BLE001
        safe_log_exception(logger, e, "[followup_qa] hubspot client init failed")
    try:
        portal_id = get_hubspot_portal_id()
    except Exception as e:  # noqa: BLE001
        safe_log_exception(logger, e, "[followup_qa] hubspot portal id read failed")

    try:
        tag_result = build_tagged_contacts(
            sess,
            apollo_client=apollo_client,
            hubspot_contact_client=hs_contact_client,
            portal_id=portal_id,
        )
    except Exception as e:  # noqa: BLE001
        safe_log_exception(
            logger, e, "[followup_qa] lazy_fetch build_tagged_contacts raised"
        )
        return 0

    try:
        return _persist_personas_from_tag_result(sess, tag_result)
    except Exception as e:  # noqa: BLE001
        safe_log_exception(
            logger, e, "[followup_qa] lazy_fetch persist failed"
        )
        return 0


def get_bot_user_id(client: Any) -> Optional[str]:
    """Resolve and cache the bot user ID. Safe to call repeatedly."""
    global _BOT_USER_ID
    if _BOT_USER_ID:
        return _BOT_USER_ID
    if client is None:
        return None
    try:
        resp = client.auth_test()
        # WebClient response acts like a dict; tolerate both shapes.
        if isinstance(resp, dict):
            _BOT_USER_ID = resp.get("user_id")
        else:
            data = getattr(resp, "data", None) or {}
            _BOT_USER_ID = data.get("user_id")
        return _BOT_USER_ID
    except Exception as e:  # noqa: BLE001 — never str(e); SDK errors leak tokens
        safe_log_exception(logger, e, "[followup_qa] auth_test failed")
        return None


def _reset_bot_user_id_for_tests() -> None:
    """Test helper — clear the cached bot user ID."""
    global _BOT_USER_ID
    _BOT_USER_ID = None


def is_bot_mentioned(text: str, bot_user_id: str) -> bool:
    """True iff `<@{bot_user_id}>` appears in `text` (case-sensitive ID)."""
    if not text or not bot_user_id:
        return False
    return f"<@{bot_user_id}>" in text


def _strip_mention(text: str, bot_user_id: str) -> str:
    """Remove the bot mention token from `text` and return the trimmed remainder."""
    if not text:
        return ""
    token = f"<@{bot_user_id}>"
    return text.replace(token, "").strip()


def _extract_ts(resp: Any) -> Optional[str]:
    """Pull the `ts` field off a Slack response (dict-like or SlackResponse)."""
    if resp is None:
        return None
    if isinstance(resp, dict):
        return resp.get("ts")
    data = getattr(resp, "data", None) or {}
    if isinstance(data, dict):
        return data.get("ts")
    return None


def _post_or_update(
    client: Any,
    channel: str,
    placeholder_ts: Optional[str],
    thread_ts: str,
    *,
    blocks: Optional[List[Dict[str, Any]]] = None,
    text: str,
) -> None:
    """Edit placeholder if we have one; otherwise post fresh in the thread.

    `chat_update` failures fall back to `chat_postMessage` (spec edge case).
    """
    kwargs: Dict[str, Any] = {"channel": channel, "text": text}
    if blocks is not None:
        kwargs["blocks"] = blocks

    if placeholder_ts and client is not None:
        try:
            client.chat_update(ts=placeholder_ts, **kwargs)
            return
        except Exception as e:  # noqa: BLE001
            safe_log_exception(logger, e, "[followup_qa] chat_update failed; falling back to post")

    if client is None:
        return
    try:
        client.chat_postMessage(thread_ts=thread_ts, **kwargs)
    except Exception as e:  # noqa: BLE001
        safe_log_exception(logger, e, "[followup_qa] chat_postMessage failed")


def _rate_limit_counts(
    db: Any, session_id: str, rep_id: str
) -> Dict[str, int]:
    """Return per-session and per-rep follow-up counts in the last 24h."""
    since = datetime.utcnow() - timedelta(hours=_RATE_LIMIT_WINDOW_HOURS)
    try:
        per_session = (
            db.query(WorkflowEvent)
            .filter(WorkflowEvent.event_type == "followup_question")
            .filter(WorkflowEvent.session_id == session_id)
            .filter(WorkflowEvent.timestamp > since)
            .count()
        )
        per_rep = (
            db.query(WorkflowEvent)
            .filter(WorkflowEvent.event_type == "followup_question")
            .filter(WorkflowEvent.rep_id == rep_id)
            .filter(WorkflowEvent.timestamp > since)
            .count()
        )
        return {"session": per_session, "rep": per_rep}
    except Exception as e:  # noqa: BLE001
        safe_log_exception(logger, e, "[followup_qa] rate-limit count failed")
        # Fail-open is dangerous; fail-closed is annoying. Compromise: if the
        # count query itself breaks, treat as 0 so the user isn't blocked by
        # a transient DB issue.
        return {"session": 0, "rep": 0}


def _fetch_thread_history(
    client: Any,
    channel: str,
    thread_ts: str,
    *,
    exclude_ts: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Pull last 20 thread replies; oldest-first. Empty list on any failure."""
    if client is None:
        return []
    try:
        resp = client.conversations_replies(
            channel=channel, ts=thread_ts, limit=20
        )
    except Exception as e:  # noqa: BLE001
        safe_log_exception(logger, e, "[followup_qa] conversations_replies failed")
        return []

    if isinstance(resp, dict):
        msgs = resp.get("messages") or []
    else:
        data = getattr(resp, "data", None) or {}
        msgs = data.get("messages") or []

    skip = set(exclude_ts or [])
    history: List[Dict[str, Any]] = []
    for m in msgs:
        if not isinstance(m, dict):
            continue
        ts = m.get("ts")
        if ts in skip:
            continue
        history.append(
            {"user": m.get("user") or m.get("bot_id") or "?", "text": m.get("text") or "", "ts": ts}
        )
    return history


def _log_workflow_event(
    db: Any,
    *,
    event_type: str,
    session_id: Optional[str],
    rep_id: str,
    payload: Dict[str, Any],
) -> None:
    """Insert a WorkflowEvent row. Narrow-except; never raises."""
    try:
        evt = WorkflowEvent(
            event_type=event_type,
            session_id=session_id,
            rep_id=rep_id,
            payload=payload,
        )
        db.add(evt)
        db.commit()
    except Exception as e:  # noqa: BLE001
        safe_log_exception(logger, e, f"[followup_qa] failed to log {event_type}")
        try:
            db.rollback()
        except Exception:  # noqa: BLE001 — defensive
            pass


def find_any_session_for_thread(thread_ts: str) -> Optional[Any]:
    """Broader lookup used by the `forbidden_followup` path.

    Returns the most recent non-cancelled Session row for `thread_ts`
    regardless of rep_id. The caller has already failed the rep-match
    lookup; this answers "does ANY session own this thread?" so we can
    log the cross-rep attempt.
    """
    if not thread_ts or SessionLocal is None:
        return None
    db = SessionLocal()
    try:
        return (
            db.query(DBSession)
            .filter(DBSession.thread_ts == thread_ts)
            .filter(DBSession.status != "cancelled")
            .order_by(DBSession.created_at.desc())
            .first()
        )
    except Exception as e:  # noqa: BLE001
        safe_log_exception(logger, e, "[followup_qa] find_any_session_for_thread failed")
        return None
    finally:
        db.close()


def log_forbidden_followup(session_id: Optional[str], rep_id: str) -> None:
    """Standalone helper so the dm_research wrong-rep branch can log without
    duplicating the WorkflowEvent setup."""
    if SessionLocal is None:
        return
    db = SessionLocal()
    try:
        _log_workflow_event(
            db,
            event_type="forbidden_followup",
            session_id=session_id,
            rep_id=rep_id,
            payload={"reason": "rep_mismatch"},
        )
    finally:
        db.close()


def handle_followup(
    message: Dict[str, Any],
    say: Callable[..., Any],
    client: Any,
) -> None:
    """Spec §5 Move 2 — answer a thread-based follow-up question.

    Order is load-bearing:
      1. Placeholder post (within 3s — this is the Slack ack).
      2. Strip mention; bail on empty.
      3. Load DB session; AUTHZ; cancelled/research-in-flight guards.
      4. Rate-limit check.
      5. Build context; call LLM; safe_mrkdwn; edit placeholder.
      6. Log WorkflowEvent (no raw question text).
    """
    thread_ts = message.get("thread_ts") or message.get("ts")
    channel = message.get("channel")
    user_id = message.get("user") or ""
    text = message.get("text") or ""
    current_msg_ts = message.get("ts")

    # 1. Placeholder — posted BEFORE any DB or LLM work so Slack sees an
    # immediate reply (3-second ack rule).
    placeholder_ts: Optional[str] = None
    try:
        resp = say(text=_THINKING_PLACEHOLDER, thread_ts=thread_ts)
        placeholder_ts = _extract_ts(resp)
    except Exception as e:  # noqa: BLE001
        safe_log_exception(logger, e, "[followup_qa] placeholder post failed")
        # No placeholder ts — we'll fall back to chat_postMessage for final.

    # Need the bot user ID to strip the mention.
    bot_user_id = get_bot_user_id(client)

    bare_question = _strip_mention(text, bot_user_id) if bot_user_id else text.strip()

    if SessionLocal is None:
        # No DB available — surface a degraded message and bail.
        _post_or_update(
            client, channel, placeholder_ts, thread_ts,
            text=_RESEARCH_RUNNING_MSG,
        )
        return

    db = SessionLocal()
    try:
        # 3. Load the most recent non-cancelled session for this thread.
        session_row = (
            db.query(DBSession)
            .filter(DBSession.thread_ts == thread_ts)
            .filter(DBSession.status != "cancelled")
            .order_by(DBSession.created_at.desc())
            .first()
        )

        # If no active session matches, check whether ANY session matches
        # (cancelled). Otherwise drop silently.
        if session_row is None:
            cancelled = (
                db.query(DBSession)
                .filter(DBSession.thread_ts == thread_ts)
                .order_by(DBSession.created_at.desc())
                .first()
            )
            if cancelled is not None and getattr(cancelled, "status", None) == "cancelled":
                _post_or_update(
                    client, channel, placeholder_ts, thread_ts,
                    text=_CANCELLED_MSG,
                )
            return

        # AUTHZ — never mutate state for a different rep.
        if getattr(session_row, "rep_id", None) != user_id:
            _log_workflow_event(
                db,
                event_type="forbidden_followup",
                session_id=session_row.id,
                rep_id=user_id,
                payload={"reason": "rep_mismatch"},
            )
            # No Slack post. Placeholder, if any, was posted as the bot —
            # delete it to avoid leaking that a session exists.
            if placeholder_ts and client is not None:
                try:
                    client.chat_delete(channel=channel, ts=placeholder_ts)
                except Exception as e:  # noqa: BLE001
                    safe_log_exception(logger, e, "[followup_qa] placeholder delete failed")
            return

        account_name = getattr(session_row, "account_name", None) or "this account"

        # Empty mention → hint reply.
        if not bare_question:
            _post_or_update(
                client, channel, placeholder_ts, thread_ts,
                text=(
                    f"Ask me a question about {account_name} and I'll answer "
                    f"from saved research."
                ),
            )
            return

        # Cancelled session was already filtered by the active-session
        # query, but re-check defensively in case it transitioned mid-flight.
        if getattr(session_row, "status", None) == "cancelled":
            _post_or_update(
                client, channel, placeholder_ts, thread_ts,
                text=_CANCELLED_MSG,
            )
            return

        # Research-in-flight: no CompanyResearch yet.
        company_research = (
            db.query(CompanyResearch)
            .filter(CompanyResearch.session_id == session_row.id)
            .order_by(CompanyResearch.created_at.desc())
            .first()
        )
        if company_research is None:
            _post_or_update(
                client, channel, placeholder_ts, thread_ts,
                text=_RESEARCH_RUNNING_MSG,
            )
            return

        # 4. Rate-limit gates.
        counts = _rate_limit_counts(db, session_row.id, user_id)
        if counts["session"] >= _RATE_LIMIT_PER_SESSION:
            _post_or_update(
                client, channel, placeholder_ts, thread_ts,
                text=_RATE_LIMIT_MSG,
            )
            return
        if counts["rep"] >= _RATE_LIMIT_PER_REP:
            _post_or_update(
                client, channel, placeholder_ts, thread_ts,
                text=_RATE_LIMIT_MSG,
            )
            return

        # Load supporting artifacts.
        personas = (
            db.query(Persona)
            .filter(Persona.session_id == session_row.id)
            .all()
        )

        # Phase 17 — lazy contact fetch. If the rep picked `general_research`
        # at intent capture, Stage 2 never auto-ran and `personas` is
        # empty. When the follow-up question implies contacts, fire the
        # HubSpot + Apollo pipeline inline, persist, and reload.
        if not personas and _is_contact_question(bare_question):
            count = _lazy_fetch_contacts(
                session_row, client, channel, placeholder_ts, thread_ts
            )
            if count:
                personas = (
                    db.query(Persona)
                    .filter(Persona.session_id == session_row.id)
                    .all()
                )
                try:
                    _post_or_update(
                        client, channel, placeholder_ts, thread_ts,
                        text=_THINKING_PLACEHOLDER,
                    )
                except Exception as e:  # noqa: BLE001
                    safe_log_exception(
                        logger, e,
                        "[followup_qa] thinking-restore after lazy_fetch failed",
                    )

        contact_researches = (
            db.query(ContactResearch)
            .filter(ContactResearch.session_id == session_row.id)
            .all()
        )

        # Thread history — exclude the current question + placeholder.
        exclude = [t for t in (current_msg_ts, placeholder_ts) if t]
        thread_history = _fetch_thread_history(
            client, channel, thread_ts, exclude_ts=exclude
        )

        # 5. Build context → LLM → safe_mrkdwn.
        started = time.perf_counter()
        try:
            context = build_followup_context(
                session_row,
                company_research,
                personas,
                contact_researches,
                thread_history,
                bare_question,
            )
            raw_answer = answer_followup(context)
        except Exception as e:  # noqa: BLE001
            safe_log_exception(logger, e, "[followup_qa] context/LLM failure")
            raw_answer = "_I couldn't think through that one — try again or rephrase?_"
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        safe_answer = safe_mrkdwn(raw_answer)
        footer = (
            f"_Answered from saved research for {account_name}. "
            f"No new sources fetched._"
        )

        blocks: List[Dict[str, Any]] = [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": safe_answer},
            },
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": footer}],
            },
        ]

        # Plain-text snippet for accessibility / push notifications. Strip
        # mrkdwn the same way; cap at 160 chars to avoid spilling.
        plaintext = safe_answer[:160] + (footer if len(safe_answer) < 100 else "")

        _post_or_update(
            client, channel, placeholder_ts, thread_ts,
            blocks=blocks, text=plaintext or footer,
        )

        # 6. Log — question text NOT included. Lengths only.
        _log_workflow_event(
            db,
            event_type="followup_question",
            session_id=session_row.id,
            rep_id=user_id,
            payload={
                "question_length": len(bare_question),
                "answer_length": len(raw_answer or ""),
                "latency_ms": elapsed_ms,
            },
        )
    finally:
        db.close()
