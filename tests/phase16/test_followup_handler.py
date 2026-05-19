"""Phase 5 (V1 Daily-Use) — Move 2 handler: routing + Slack integration.

Covers TDD plan Phase 5 (20 tests). Spec §5 Move 2 + §7 success criteria
5-12. Mirrors the in-memory sqlite + dual SessionLocal patching pattern
established in Phases 1-3.

Security invariants verified here:
- AUTHZ — wrong-rep messages produce no Slack post and log
  `forbidden_followup`.
- safe_mrkdwn — `<http://evil|click>` payloads are stripped from LLM output
  before posting.
- Log hygiene — raw question text is NEVER logged or stored.
- Slack 3s ack rule — placeholder posted before any LLM call.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import (
    Base,
    CompanyResearch,
    ContactResearch,
    Persona,
    Session as DBSession,
    WorkflowEvent,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def db_sessionmaker(db_engine):
    return sessionmaker(bind=db_engine, autoflush=False, autocommit=False)


@pytest.fixture
def patched_db(mocker, db_sessionmaker):
    """Patch every module that captured SessionLocal at import time."""
    mocker.patch("src.db.session.SessionLocal", db_sessionmaker)
    mocker.patch("src.main.SessionLocal", db_sessionmaker)
    mocker.patch("src.handlers.followup_qa.SessionLocal", db_sessionmaker)
    return db_sessionmaker


@pytest.fixture(autouse=True)
def _reset_bot_id():
    """Don't let auth_test results bleed across tests."""
    from src.handlers import followup_qa
    followup_qa._reset_bot_user_id_for_tests()
    yield
    followup_qa._reset_bot_user_id_for_tests()


def _seed_session(sm, *, session_id="sess-1", rep_id="U_REP", thread_ts="1700000000.0001",
                  account_name="Sysco", channel="D_CHAN_1", status="active"):
    db = sm()
    try:
        row = DBSession(
            id=session_id,
            account_name=account_name,
            rep_id=rep_id,
            channel_id=channel,
            status=status,
        )
        row.thread_ts = thread_ts
        db.add(row)
        db.commit()
    finally:
        db.close()


def _seed_company_research(sm, *, session_id="sess-1", account_name="Sysco"):
    db = sm()
    try:
        cr = CompanyResearch(
            session_id=session_id,
            account_name=account_name,
            facility_count=87,
            facility_count_note="from 10-K",
            board_initiatives=[],
            trigger_events=[],
            automation_vendors=[],
            research_gaps=[],
            raw_research_text="Sysco summary.",
        )
        db.add(cr)
        db.commit()
    finally:
        db.close()


def _seed_persona(sm, *, session_id="sess-1", first_name="Sarah", last_name="Chen",
                   title="Director of CI", persona_type="TDM"):
    db = sm()
    try:
        p = Persona(
            session_id=session_id,
            first_name=first_name,
            last_name=last_name,
            title=title,
            persona_type=persona_type,
            seniority="Director",
            priority_score="High",
        )
        db.add(p)
        db.commit()
    finally:
        db.close()


def _make_client(bot_user_id="UBOT123"):
    client = MagicMock()
    client.auth_test.return_value = {"user_id": bot_user_id}
    client.chat_update.return_value = {"ok": True}
    client.chat_postMessage.return_value = {"ok": True, "ts": "1700000001.9999"}
    client.conversations_replies.return_value = {"messages": []}
    return client


def _make_llm_response(content: str) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"choices": [{"message": {"content": content}}]}
    return resp


# ---------------------------------------------------------------------------
# 1-3. Mention detection
# ---------------------------------------------------------------------------


def test_mention_detection_finds_bot_id():
    from src.handlers.followup_qa import is_bot_mentioned

    assert is_bot_mentioned("hey <@UBOT123> who first?", "UBOT123") is True


def test_mention_detection_ignores_wrong_id():
    from src.handlers.followup_qa import is_bot_mentioned

    assert is_bot_mentioned("hey <@UOTHER> who first?", "UBOT123") is False


def test_mention_detection_handles_no_mention():
    from src.handlers.followup_qa import is_bot_mentioned

    assert is_bot_mentioned("just musing here", "UBOT123") is False


# ---------------------------------------------------------------------------
# 4-7. dm_research thread-reply routing
# ---------------------------------------------------------------------------


def test_thread_reply_with_mention_routes_to_handle_followup(mocker, patched_db):
    """Reply in an existing thread w/ `@bot` → handle_followup invoked,
    create_session NOT called (no new research)."""
    _seed_session(patched_db, rep_id="U_REP", thread_ts="1700000000.0001")

    mock_followup = mocker.patch("src.handlers.dm_research.handle_followup")
    mock_create = mocker.patch("src.handlers.dm_research.create_session")
    mocker.patch(
        "src.handlers.dm_research.get_bot_user_id", return_value="UBOT123"
    )

    from src.handlers.dm_research import handle_research_dm

    client = _make_client()
    say = MagicMock()
    handle_research_dm(
        message={
            "text": "<@UBOT123> who first?",
            "user": "U_REP",
            "ts": "1700000000.0050",
            "thread_ts": "1700000000.0001",
            "channel": "D_CHAN_1",
        },
        say=say,
        client=client,
    )

    mock_followup.assert_called_once()
    mock_create.assert_not_called()


def test_thread_reply_without_mention_is_ignored(mocker, patched_db):
    _seed_session(patched_db, rep_id="U_REP", thread_ts="1700000000.0002")

    mock_followup = mocker.patch("src.handlers.dm_research.handle_followup")
    mock_create = mocker.patch("src.handlers.dm_research.create_session")
    mocker.patch(
        "src.handlers.dm_research.get_bot_user_id", return_value="UBOT123"
    )

    from src.handlers.dm_research import handle_research_dm

    client = _make_client()
    say = MagicMock()
    handle_research_dm(
        message={
            "text": "just thinking out loud",
            "user": "U_REP",
            "ts": "1700000000.0051",
            "thread_ts": "1700000000.0002",
            "channel": "D_CHAN_1",
        },
        say=say,
        client=client,
    )

    mock_followup.assert_not_called()
    mock_create.assert_not_called()
    say.assert_not_called()


def test_thread_reply_unknown_session_is_ignored(mocker, patched_db):
    """thread_ts present but no Session row matches → silent. Crucially
    does NOT fall through to new-research."""
    mock_followup = mocker.patch("src.handlers.dm_research.handle_followup")
    mock_create = mocker.patch("src.handlers.dm_research.create_session")
    mocker.patch(
        "src.handlers.dm_research.get_bot_user_id", return_value="UBOT123"
    )

    from src.handlers.dm_research import handle_research_dm

    client = _make_client()
    say = MagicMock()
    handle_research_dm(
        message={
            "text": "<@UBOT123> hello?",
            "user": "U_REP",
            "ts": "1700000000.0052",
            "thread_ts": "1700000999.0001",
            "channel": "D_CHAN_1",
        },
        say=say,
        client=client,
    )

    mock_followup.assert_not_called()
    mock_create.assert_not_called()


def test_top_level_dm_unchanged(mocker, patched_db):
    """No `thread_ts` → existing post-Phase-3 path runs: diff check, then
    (with no fresh snapshot) the intent-capture card. Critically,
    create_session IS called and handle_followup is NOT."""
    mocker.patch(
        "src.handlers.dm_research.should_show_diff_front_door",
        return_value=None,
    )
    mocker.patch(
        "src.handlers.dm_research.is_account_ambiguous", return_value=None
    )
    mock_followup = mocker.patch("src.handlers.dm_research.handle_followup")
    spy_create = mocker.spy(
        __import__("src.handlers.dm_research", fromlist=["create_session"]),
        "create_session",
    )

    from src.handlers.dm_research import handle_research_dm

    client = _make_client()
    say = MagicMock(return_value={"ts": "1700000000.0099"})
    handle_research_dm(
        message={
            "text": "Sysco",
            "user": "U_REP",
            "ts": "1700000000.0060",
            "channel": "D_CHAN_1",
            # no thread_ts → top-level DM
        },
        say=say,
        client=client,
    )

    mock_followup.assert_not_called()
    assert spy_create.call_count == 1


def test_thread_reply_wrong_rep_is_ignored(mocker, patched_db):
    """Session belongs to rep A; rep B replies with mention.
    `forbidden_followup` logged; no Slack content from handle_followup."""
    _seed_session(
        patched_db,
        rep_id="U_REP_A",
        thread_ts="1700000000.0003",
        session_id="sess-wrongrep",
    )

    mock_followup = mocker.patch("src.handlers.dm_research.handle_followup")
    mocker.patch(
        "src.handlers.dm_research.get_bot_user_id", return_value="UBOT123"
    )

    from src.handlers.dm_research import handle_research_dm

    client = _make_client()
    say = MagicMock()
    handle_research_dm(
        message={
            "text": "<@UBOT123> sneak peek?",
            "user": "U_REP_B",  # different rep
            "ts": "1700000000.0061",
            "thread_ts": "1700000000.0003",
            "channel": "D_CHAN_1",
        },
        say=say,
        client=client,
    )

    mock_followup.assert_not_called()

    # forbidden_followup event must be logged.
    db = patched_db()
    try:
        evts = (
            db.query(WorkflowEvent)
            .filter(WorkflowEvent.event_type == "forbidden_followup")
            .all()
        )
    finally:
        db.close()
    assert len(evts) == 1
    assert evts[0].rep_id == "U_REP_B"
    assert evts[0].session_id == "sess-wrongrep"


# ---------------------------------------------------------------------------
# 9-20. handle_followup
# ---------------------------------------------------------------------------


def _base_message(text="<@UBOT123> who first?", thread_ts="1700000000.0001",
                  channel="D_CHAN_1", user="U_REP", ts="1700000000.0099"):
    return {
        "text": text,
        "user": user,
        "ts": ts,
        "thread_ts": thread_ts,
        "channel": channel,
    }


def test_handler_posts_placeholder_within_3s(mocker, monkeypatch, patched_db):
    """Placeholder must be posted before the LLM call. We mock httpx to
    introduce a tiny delay and assert say() fired first."""
    from src.config import settings
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-test")

    _seed_session(patched_db)
    _seed_company_research(patched_db)

    call_order = []

    def _delayed_post(*args, **kwargs):
        time.sleep(0.05)
        call_order.append("llm")
        return _make_llm_response("answer text")

    mocker.patch("src.agents.followup_agent.httpx.post", side_effect=_delayed_post)

    say = MagicMock(side_effect=lambda **kw: (call_order.append("placeholder"),
                                              {"ts": "1700000000.5000"})[1])
    client = _make_client()

    from src.handlers.followup_qa import handle_followup
    handle_followup(message=_base_message(), say=say, client=client)

    assert call_order, "neither placeholder nor llm fired"
    assert call_order[0] == "placeholder"


def test_handler_edits_placeholder_with_answer(mocker, monkeypatch, patched_db):
    from src.config import settings
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-test")

    _seed_session(patched_db)
    _seed_company_research(patched_db)

    mocker.patch(
        "src.agents.followup_agent.httpx.post",
        return_value=_make_llm_response("Sarah Chen is the top priority."),
    )

    say = MagicMock(return_value={"ts": "1700000000.5555"})
    client = _make_client()

    from src.handlers.followup_qa import handle_followup
    handle_followup(message=_base_message(), say=say, client=client)

    client.chat_update.assert_called()
    update_kwargs = client.chat_update.call_args.kwargs
    assert update_kwargs.get("ts") == "1700000000.5555"


def test_handler_falls_back_to_post_on_update_failure(mocker, monkeypatch, patched_db):
    from src.config import settings
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-test")

    _seed_session(patched_db)
    _seed_company_research(patched_db)

    mocker.patch(
        "src.agents.followup_agent.httpx.post",
        return_value=_make_llm_response("answer"),
    )

    say = MagicMock(return_value={"ts": "1700000000.5555"})
    client = _make_client()
    client.chat_update.side_effect = RuntimeError("update failed")

    from src.handlers.followup_qa import handle_followup
    handle_followup(message=_base_message(), say=say, client=client)

    client.chat_postMessage.assert_called()
    post_kwargs = client.chat_postMessage.call_args.kwargs
    assert post_kwargs.get("thread_ts") == "1700000000.0001"


def test_handler_runs_answer_through_safe_mrkdwn(mocker, monkeypatch, patched_db):
    from src.config import settings
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-test")

    _seed_session(patched_db)
    _seed_company_research(patched_db)

    # LLM returns an mrkdwn-link payload — must be stripped before posting.
    mocker.patch(
        "src.agents.followup_agent.httpx.post",
        return_value=_make_llm_response("<http://evil|click>"),
    )

    say = MagicMock(return_value={"ts": "1700000000.5555"})
    client = _make_client()

    from src.handlers.followup_qa import handle_followup
    handle_followup(message=_base_message(), say=say, client=client)

    client.chat_update.assert_called()
    update_kwargs = client.chat_update.call_args.kwargs
    blocks = update_kwargs.get("blocks") or []
    section_text = ""
    for b in blocks:
        if b.get("type") == "section":
            section_text = b.get("text", {}).get("text", "")
            break

    for ch in ("<", ">", "|"):
        assert ch not in section_text, f"{ch!r} not stripped from posted text"


def test_handler_logs_workflow_event_with_latency(mocker, monkeypatch, patched_db):
    from src.config import settings
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-test")

    _seed_session(patched_db)
    _seed_company_research(patched_db)

    mocker.patch(
        "src.agents.followup_agent.httpx.post",
        return_value=_make_llm_response("ok"),
    )

    say = MagicMock(return_value={"ts": "1700000000.5555"})
    client = _make_client()

    from src.handlers.followup_qa import handle_followup
    handle_followup(message=_base_message(), say=say, client=client)

    db = patched_db()
    try:
        evts = (
            db.query(WorkflowEvent)
            .filter(WorkflowEvent.event_type == "followup_question")
            .all()
        )
    finally:
        db.close()

    assert len(evts) == 1
    payload = evts[0].payload or {}
    assert "latency_ms" in payload
    assert isinstance(payload["latency_ms"], int)


def test_handler_does_not_log_raw_question_text(mocker, monkeypatch, patched_db, caplog):
    from src.config import settings
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-test")

    _seed_session(patched_db)
    _seed_company_research(patched_db)

    mocker.patch(
        "src.agents.followup_agent.httpx.post",
        return_value=_make_llm_response("ok"),
    )

    sentinel = "SUPER-SECRET-PASTED-TOKEN-9XYZQ"
    say = MagicMock(return_value={"ts": "1700000000.5555"})
    client = _make_client()

    from src.handlers.followup_qa import handle_followup

    with caplog.at_level(logging.DEBUG):
        handle_followup(
            message=_base_message(text=f"<@UBOT123> {sentinel}"),
            say=say,
            client=client,
        )

    # Not in any log line.
    log_text = "\n".join(r.getMessage() for r in caplog.records)
    assert sentinel not in log_text

    # Not in the WorkflowEvent payload either.
    db = patched_db()
    try:
        evts = (
            db.query(WorkflowEvent)
            .filter(WorkflowEvent.event_type == "followup_question")
            .all()
        )
    finally:
        db.close()
    assert len(evts) == 1
    payload_str = str(evts[0].payload or {})
    assert sentinel not in payload_str


def test_handler_handles_research_still_running(mocker, monkeypatch, patched_db):
    """Session exists, no CompanyResearch row → message says still running;
    OpenRouter is NEVER hit."""
    from src.config import settings
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-test")

    _seed_session(patched_db)  # no CompanyResearch seeded

    mock_post = mocker.patch("src.agents.followup_agent.httpx.post")

    say = MagicMock(return_value={"ts": "1700000000.5555"})
    client = _make_client()

    from src.handlers.followup_qa import handle_followup
    handle_followup(message=_base_message(), say=say, client=client)

    mock_post.assert_not_called()

    # Check the user-facing message via chat_update or chat_postMessage.
    posted_text = ""
    if client.chat_update.called:
        posted_text = client.chat_update.call_args.kwargs.get("text", "")
    elif client.chat_postMessage.called:
        posted_text = client.chat_postMessage.call_args.kwargs.get("text", "")
    assert "still finishing" in posted_text.lower() or "still finishing the initial research" in posted_text.lower()


def test_handler_handles_cancelled_session(mocker, monkeypatch, patched_db):
    """status='cancelled' → cancellation message; no LLM call."""
    from src.config import settings
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-test")

    _seed_session(patched_db, status="cancelled")
    _seed_company_research(patched_db)

    mock_post = mocker.patch("src.agents.followup_agent.httpx.post")

    say = MagicMock(return_value={"ts": "1700000000.5555"})
    client = _make_client()

    from src.handlers.followup_qa import handle_followup
    handle_followup(message=_base_message(), say=say, client=client)

    mock_post.assert_not_called()
    posted_text = ""
    if client.chat_update.called:
        posted_text = client.chat_update.call_args.kwargs.get("text", "")
    elif client.chat_postMessage.called:
        posted_text = client.chat_postMessage.call_args.kwargs.get("text", "")
    assert "cancel" in posted_text.lower()


def test_handler_handles_empty_question_after_mention(mocker, monkeypatch, patched_db):
    """Message is exactly `<@UBOT123>` → hint message; no LLM call."""
    from src.config import settings
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-test")

    _seed_session(patched_db)
    _seed_company_research(patched_db)

    mock_post = mocker.patch("src.agents.followup_agent.httpx.post")

    say = MagicMock(return_value={"ts": "1700000000.5555"})
    client = _make_client()

    from src.handlers.followup_qa import handle_followup
    handle_followup(message=_base_message(text="<@UBOT123>"), say=say, client=client)

    mock_post.assert_not_called()
    posted_text = ""
    if client.chat_update.called:
        posted_text = client.chat_update.call_args.kwargs.get("text", "")
    elif client.chat_postMessage.called:
        posted_text = client.chat_postMessage.call_args.kwargs.get("text", "")
    assert "ask me a question" in posted_text.lower()
    assert "sysco" in posted_text.lower()


def test_handler_respects_per_session_rate_limit(mocker, monkeypatch, patched_db):
    """20 prior `followup_question` events for the session in 24h → 21st
    returns rate-limit message; no LLM call."""
    from src.config import settings
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-test")

    _seed_session(patched_db)
    _seed_company_research(patched_db)

    # Seed 20 prior events.
    db = patched_db()
    try:
        for i in range(20):
            db.add(WorkflowEvent(
                event_type="followup_question",
                session_id="sess-1",
                rep_id="U_REP",
                payload={"question_length": 10},
                timestamp=datetime.utcnow() - timedelta(minutes=i + 1),
            ))
        db.commit()
    finally:
        db.close()

    mock_post = mocker.patch("src.agents.followup_agent.httpx.post")

    say = MagicMock(return_value={"ts": "1700000000.5555"})
    client = _make_client()

    from src.handlers.followup_qa import handle_followup
    handle_followup(message=_base_message(), say=say, client=client)

    mock_post.assert_not_called()
    posted_text = ""
    if client.chat_update.called:
        posted_text = client.chat_update.call_args.kwargs.get("text", "")
    elif client.chat_postMessage.called:
        posted_text = client.chat_postMessage.call_args.kwargs.get("text", "")
    assert "rate limit" in posted_text.lower()


def test_handler_respects_per_rep_rate_limit(mocker, monkeypatch, patched_db):
    """100 prior `followup_question` events for the rep in 24h → next
    returns rate-limit; no LLM call."""
    from src.config import settings
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-test")

    _seed_session(patched_db)
    _seed_company_research(patched_db)

    # Seed 100 prior events under different session_ids but same rep_id.
    db = patched_db()
    try:
        for i in range(100):
            db.add(WorkflowEvent(
                event_type="followup_question",
                session_id=f"other-{i}",
                rep_id="U_REP",
                payload={"question_length": 10},
                timestamp=datetime.utcnow() - timedelta(minutes=i + 1),
            ))
        db.commit()
    finally:
        db.close()

    mock_post = mocker.patch("src.agents.followup_agent.httpx.post")

    say = MagicMock(return_value={"ts": "1700000000.5555"})
    client = _make_client()

    from src.handlers.followup_qa import handle_followup
    handle_followup(message=_base_message(), say=say, client=client)

    mock_post.assert_not_called()
    posted_text = ""
    if client.chat_update.called:
        posted_text = client.chat_update.call_args.kwargs.get("text", "")
    elif client.chat_postMessage.called:
        posted_text = client.chat_postMessage.call_args.kwargs.get("text", "")
    assert "rate limit" in posted_text.lower()


def test_handler_footer_line_present(mocker, monkeypatch, patched_db):
    """Final posted message contains the exact footer mandated by §5 Move 2."""
    from src.config import settings
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-test")

    _seed_session(patched_db, account_name="Volvo Group")
    _seed_company_research(patched_db, account_name="Volvo Group")

    mocker.patch(
        "src.agents.followup_agent.httpx.post",
        return_value=_make_llm_response("Sarah is top priority."),
    )

    say = MagicMock(return_value={"ts": "1700000000.5555"})
    client = _make_client()

    from src.handlers.followup_qa import handle_followup
    handle_followup(message=_base_message(), say=say, client=client)

    client.chat_update.assert_called()
    update_kwargs = client.chat_update.call_args.kwargs
    blocks = update_kwargs.get("blocks") or []
    rendered = ""
    for b in blocks:
        if b.get("type") == "context":
            for el in b.get("elements", []):
                rendered += el.get("text", "")
        elif b.get("type") == "section":
            rendered += b.get("text", {}).get("text", "")

    expected = "_Answered from saved research for Volvo Group. No new sources fetched._"
    assert expected in rendered
