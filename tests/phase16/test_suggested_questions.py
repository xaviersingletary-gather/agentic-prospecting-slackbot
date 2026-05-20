"""Phase 6 (V1 Daily-Use) — Move 3: suggested follow-ups.

Covers spec §5 Move 3 + §7 Success Criteria 13-15.

Mirrors the in-memory sqlite + dual SessionLocal patching pattern from
Phases 1-5. External LLM, Slack, and DB clients are mocked — no real
network or Postgres traffic.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import (
    Base,
    CompanyResearch,
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
    mocker.patch(
        "src.handlers.suggested_question_click.SessionLocal", db_sessionmaker
    )
    mocker.patch("src.research.runner.SessionLocal", db_sessionmaker, create=True)
    return db_sessionmaker


@pytest.fixture(autouse=True)
def _reset_bot_id():
    """Don't let auth_test results bleed across tests."""
    from src.handlers import followup_qa
    followup_qa._reset_bot_user_id_for_tests()
    yield
    followup_qa._reset_bot_user_id_for_tests()


def _make_llm_response(content: str) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"choices": [{"message": {"content": content}}]}
    return resp


def _make_client(bot_user_id="UBOT123"):
    client = MagicMock()
    client.auth_test.return_value = {"user_id": bot_user_id}
    client.chat_update.return_value = {"ok": True}
    client.chat_postMessage.return_value = {"ok": True, "ts": "1700000099.0001"}
    client.conversations_replies.return_value = {"messages": []}
    return client


def _fixture_findings():
    return {
        "account_name": "Volvo Group",
        "facility_count": 42,
        "trigger_events": [
            {"description": "Memphis DC ramp announced"},
        ],
        "board_initiatives": [
            {"title": "Inventory accuracy 2026 priority"},
        ],
        "automation_vendors": [],
        "research_gaps": [],
        "raw_research_text": "Volvo Group raw text…",
    }


def _seed_session(sm, *, session_id="sess-1", rep_id="U_REP",
                  thread_ts="1700000000.0001", account_name="Volvo Group",
                  channel="D_CHAN_1", status="active"):
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


def _seed_company_research(sm, *, session_id="sess-1", account_name="Volvo Group"):
    db = sm()
    try:
        cr = CompanyResearch(
            session_id=session_id,
            account_name=account_name,
            facility_count=42,
            board_initiatives=[{"title": "Inventory accuracy"}],
            trigger_events=[{"description": "Memphis DC ramp"}],
            automation_vendors=[],
            research_gaps=[],
            raw_research_text="raw",
        )
        db.add(cr)
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Generator — 5 tests
# ---------------------------------------------------------------------------


def test_generator_returns_3_or_4_questions_on_success(mocker, monkeypatch):
    from src.config import settings
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-test")

    questions = [
        "How does the Memphis DC ramp change priority for Sarah Chen?",
        "Which Inventory accuracy initiative angle lands best?",
        "What gap does the Q1 earnings call expose?",
        "Who owns rollout — Volvo Group Network Ops?",
    ]
    mocker.patch(
        "src.agents.suggested_questions.httpx.post",
        return_value=_make_llm_response(json.dumps(questions)),
    )

    from src.agents.suggested_questions import generate_suggested_questions

    result = generate_suggested_questions(_fixture_findings(), personas=[])
    assert 3 <= len(result) <= 4
    assert result == questions


def test_generator_questions_reference_specific_findings(mocker, monkeypatch):
    """The generator must pass the LLM's output through unchanged when valid.

    We verify the function is a transparent conduit — if the LLM outputs
    questions that reference named entities, those references survive.
    """
    from src.config import settings
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-test")

    questions = [
        "Lead with the Memphis DC ramp or earnings call?",
        "Sarah Chen — what hook for the Inventory accuracy initiative?",
        "Who else at Volvo Group should we pull in?",
    ]
    mocker.patch(
        "src.agents.suggested_questions.httpx.post",
        return_value=_make_llm_response(json.dumps(questions)),
    )

    from src.agents.suggested_questions import generate_suggested_questions

    result = generate_suggested_questions(
        _fixture_findings(),
        personas=[{"first_name": "Sarah", "last_name": "Chen",
                   "title": "Director of CI", "persona_type": "TDM"}],
    )

    # Spec contract: each returned question references a specific finding
    # OR a named contact. Memphis / Sarah Chen / Inventory accuracy are
    # all valid entity anchors from the fixture.
    entity_anchors = ["Memphis", "Sarah Chen", "Inventory accuracy", "Volvo Group"]
    for q in result:
        assert any(anchor in q for anchor in entity_anchors), (
            f"question {q!r} dropped all entity anchors"
        )


def test_generator_falls_back_on_json_parse_failure(mocker, monkeypatch):
    from src.config import settings
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-test")

    mocker.patch(
        "src.agents.suggested_questions.httpx.post",
        return_value=_make_llm_response("not json"),
    )

    from src.agents.suggested_questions import (
        FALLBACK_QUESTIONS,
        generate_suggested_questions,
    )

    result = generate_suggested_questions(_fixture_findings(), personas=[])
    assert result == FALLBACK_QUESTIONS
    assert len(result) == 3


def test_generator_falls_back_on_timeout(mocker, monkeypatch):
    from src.config import settings
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-test")

    mocker.patch(
        "src.agents.suggested_questions.httpx.post",
        side_effect=httpx.TimeoutException("boom"),
    )

    from src.agents.suggested_questions import (
        FALLBACK_QUESTIONS,
        generate_suggested_questions,
    )

    result = generate_suggested_questions(_fixture_findings(), personas=[])
    assert result == FALLBACK_QUESTIONS


def test_generator_respects_5s_timeout(mocker, monkeypatch):
    from src.config import settings
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-test")

    post_mock = mocker.patch(
        "src.agents.suggested_questions.httpx.post",
        return_value=_make_llm_response(json.dumps([
            "q1", "q2", "q3",
        ])),
    )

    from src.agents.suggested_questions import generate_suggested_questions
    generate_suggested_questions(_fixture_findings(), personas=[])

    assert post_mock.call_args is not None
    kwargs = post_mock.call_args.kwargs
    assert kwargs.get("timeout") == 5


# ---------------------------------------------------------------------------
# Block — 2 tests
# ---------------------------------------------------------------------------


def test_block_renders_one_button_per_question():
    from src.integrations.slack_blocks import suggested_questions_block

    questions = ["q1", "q2", "q3"]
    blocks = suggested_questions_block(questions)
    assert len(blocks) == 1
    assert blocks[0]["type"] == "actions"
    assert len(blocks[0]["elements"]) == 3


def test_block_action_ids_are_suggested_question():
    from src.integrations.slack_blocks import suggested_questions_block

    questions = ["q1", "q2", "q3", "q4"]
    blocks = suggested_questions_block(questions)
    seen_ids = set()
    for el in blocks[0]["elements"]:
        aid = el["action_id"]
        assert aid.startswith("suggested_question_")
        assert aid not in seen_ids, "action_ids must be unique (Slack rejects dupes)"
        seen_ids.add(aid)
        assert el["type"] == "button"


# ---------------------------------------------------------------------------
# Brief render — 2 tests
# ---------------------------------------------------------------------------


def test_brief_includes_suggested_questions_block(mocker, patched_db):
    """After `run_account_research`, posted blocks include the actions
    block whose action_id is `suggested_question`."""
    from src.research.sessions import ResearchSession

    # Patch findings build so we don't hit Exa/OpenRouter.
    fake_findings = _fixture_findings()
    mocker.patch(
        "src.research.runner.build_findings", return_value=fake_findings
    )
    mocker.patch("src.research.runner.get_latest_snapshot", return_value=None)
    mocker.patch("src.research.runner.save_snapshot", return_value=None)

    # Patch the generator to return a known list.
    fixture_questions = [
        "Memphis DC ramp — who do I call?",
        "Sarah Chen angle?",
        "Volvo Group inventory accuracy — best hook?",
    ]
    mocker.patch(
        "src.research.runner.generate_suggested_questions",
        return_value=fixture_questions,
    )
    mocker.patch(
        "src.research.runner.is_fallback", return_value=False
    )

    session = ResearchSession(
        session_id="sess-brief-1", rep_id="U_REP", account_name="Volvo Group"
    )

    posted = {}

    def post(blocks, text):
        posted["blocks"] = blocks
        posted["text"] = text

    from src.research.runner import run_account_research
    run_account_research(session, post)

    blocks = posted["blocks"]
    actions = [
        b for b in blocks
        if b.get("type") == "actions"
        and any(
            (el.get("action_id") or "").startswith("suggested_question_")
            for el in b.get("elements", [])
        )
    ]
    assert len(actions) == 1
    assert len(actions[0]["elements"]) == 3


def test_brief_ships_without_block_if_generator_fails(mocker, patched_db):
    """Generator raises → brief still posts; actions block omitted; a
    `suggested_questions_failed` WorkflowEvent is logged."""
    from src.research.sessions import ResearchSession

    mocker.patch(
        "src.research.runner.build_findings",
        return_value=_fixture_findings(),
    )
    mocker.patch("src.research.runner.get_latest_snapshot", return_value=None)
    mocker.patch("src.research.runner.save_snapshot", return_value=None)

    mocker.patch(
        "src.research.runner.generate_suggested_questions",
        side_effect=RuntimeError("LLM exploded"),
    )

    session = ResearchSession(
        session_id="sess-brief-fail", rep_id="U_REP",
        account_name="Volvo Group",
    )

    posted = {}

    def post(blocks, text):
        posted["blocks"] = blocks

    from src.research.runner import run_account_research
    run_account_research(session, post)

    # Brief still shipped — `post` was called.
    assert "blocks" in posted
    # Actions block (suggested_question) NOT present.
    for b in posted["blocks"]:
        if b.get("type") == "actions":
            for el in b.get("elements", []):
                assert not (el.get("action_id") or "").startswith("suggested_question_")

    # `suggested_questions_failed` event landed.
    db = patched_db()
    try:
        evts = (
            db.query(WorkflowEvent)
            .filter(WorkflowEvent.event_type == "suggested_questions_failed")
            .all()
        )
    finally:
        db.close()
    assert len(evts) == 1


# ---------------------------------------------------------------------------
# Click handler — 3 tests
# ---------------------------------------------------------------------------


def _click_body(question="Sarah Chen — angle?", user_id="U_REP",
                channel_id="D_CHAN_1", thread_ts="1700000000.0001"):
    return {
        "actions": [{"value": question, "action_id": "suggested_question_0"}],
        "user": {"id": user_id},
        "channel": {"id": channel_id},
        "message": {"ts": thread_ts, "thread_ts": thread_ts},
    }


def test_suggested_question_click_routes_through_handle_followup(
    mocker, patched_db
):
    _seed_session(patched_db)
    _seed_company_research(patched_db)

    mock_followup = mocker.patch(
        "src.handlers.suggested_question_click.handle_followup"
    )

    from src.handlers.suggested_question_click import handle_suggested_question

    ack = MagicMock()
    client = _make_client()
    handle_suggested_question(
        ack=ack,
        body=_click_body(question="Memphis DC — who first?"),
        client=client,
    )

    ack.assert_called_once()
    mock_followup.assert_called_once()
    call_kwargs = mock_followup.call_args.kwargs
    msg = call_kwargs.get("message") or (
        mock_followup.call_args.args[0]
        if mock_followup.call_args.args
        else None
    )
    assert msg is not None
    assert "Memphis DC" in msg["text"]
    assert msg["user"] == "U_REP"
    assert msg["thread_ts"] == "1700000000.0001"
    assert msg["channel"] == "D_CHAN_1"


def test_suggested_question_click_logs_event(mocker, patched_db):
    _seed_session(patched_db)
    _seed_company_research(patched_db)

    mocker.patch(
        "src.handlers.suggested_question_click.handle_followup"
    )

    from src.handlers.suggested_question_click import handle_suggested_question

    handle_suggested_question(
        ack=MagicMock(),
        body=_click_body(question="Walk me through the Memphis DC ramp"),
        client=_make_client(),
    )

    db = patched_db()
    try:
        evts = (
            db.query(WorkflowEvent)
            .filter(WorkflowEvent.event_type == "suggested_question_clicked")
            .all()
        )
    finally:
        db.close()
    assert len(evts) == 1
    payload = evts[0].payload or {}
    assert payload.get("question_text") == "Walk me through the Memphis DC ramp"


def test_suggested_question_click_respects_rate_limit(mocker, monkeypatch, patched_db):
    """20 prior `followup_question` events for the session → the click's
    delegation into `handle_followup` short-circuits at the rate-limit
    branch. No httpx.post calls downstream."""
    from src.config import settings
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-test")

    _seed_session(patched_db)
    _seed_company_research(patched_db)

    # Seed 20 followup_question events for the session.
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

    # Guard: no LLM call should be made.
    mock_post = mocker.patch("src.agents.followup_agent.httpx.post")
    # And no suggested_questions LLM call either (defensive).
    mocker.patch("src.agents.suggested_questions.httpx.post")

    from src.handlers.suggested_question_click import handle_suggested_question

    client = _make_client()
    handle_suggested_question(
        ack=MagicMock(),
        body=_click_body(question="who first?"),
        client=client,
    )

    mock_post.assert_not_called()

    # The rate-limit message lands via chat_postMessage (placeholder path
    # in handle_followup). Inspect its text.
    posted_text = ""
    if client.chat_postMessage.called:
        for call in client.chat_postMessage.call_args_list:
            posted_text += call.kwargs.get("text", "") + "\n"
    if client.chat_update.called:
        posted_text += client.chat_update.call_args.kwargs.get("text", "")
    assert "rate limit" in posted_text.lower()
