"""Phase 7 (V1 Daily-Use) — End-to-end integration.

Seven canonical-flow tests trace spec §3:

  1. First-time account (ambiguous) → intent card → submit intent → research
  2. Repeat account (recent snapshot) → diff front-door → dig into new
  3. Follow-up Q&A after a brief lands
  4. Suggested-question click equivalent to typed `@`-mention
  5. Unauthorized user is silent + logged
  6. Q&A path never touches Exa/Apollo/HubSpot
  7. Captured intent ("renewal") reaches the agent prompt body

Hermetic: in-memory sqlite, mocked Slack client, mocked LLM. The
SessionLocal patching matches earlier Phase 16 tests so the DB writes
that production code performs land on our in-memory engine.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

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
    """Point every module that captured SessionLocal at our in-memory engine."""
    mocker.patch("src.db.session.SessionLocal", db_sessionmaker)
    mocker.patch("src.main.SessionLocal", db_sessionmaker)
    mocker.patch("src.handlers.followup_qa.SessionLocal", db_sessionmaker)
    mocker.patch(
        "src.handlers.suggested_question_click.SessionLocal", db_sessionmaker
    )
    return db_sessionmaker


@pytest.fixture(autouse=True)
def _reset_module_state():
    from src.handlers import followup_qa, intent_capture
    from src.research import sessions as sessions_module

    followup_qa._reset_bot_user_id_for_tests()
    intent_capture._reset_cache_for_tests()
    sessions_module._reset_for_tests()
    yield
    followup_qa._reset_bot_user_id_for_tests()
    intent_capture._reset_cache_for_tests()
    sessions_module._reset_for_tests()


@pytest.fixture(autouse=True)
def _isolate_snapshot_dir(tmp_path, monkeypatch):
    """Pin snapshots to a per-test tmp dir so the JSONL store stays hermetic."""
    monkeypatch.setenv("ACCOUNT_SNAPSHOT_DIR", str(tmp_path))


@pytest.fixture(autouse=True)
def _patch_log_event(mocker, db_sessionmaker):
    """`src.main.log_event` opens its own DB session via `get_session`.
    Patch that helper to use our in-memory engine.
    """
    from contextlib import contextmanager

    @contextmanager
    def _ctx():
        db = db_sessionmaker()
        try:
            yield db
        finally:
            db.close()

    mocker.patch("src.main.get_session", _ctx)
    yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(bot_user_id="UBOT123"):
    client = MagicMock()
    client.auth_test.return_value = {"user_id": bot_user_id}
    client.chat_update.return_value = {"ok": True}
    client.chat_postMessage.return_value = {"ok": True, "ts": "1700009999.0001"}
    client.conversations_replies.return_value = {"messages": []}
    return client


def _make_llm_response(content: str) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"choices": [{"message": {"content": content}}]}
    return resp


def _make_openai_response(content: str):
    """Mock the OpenAI-SDK response shape used by findings_builder."""
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _seed_snapshot_file(snapshot_dir, account_name, *, age_days=3, findings=None):
    """Write a fake snapshot JSONL file the way save_snapshot would."""
    from src.memory.snapshots import normalize_account_key

    key = normalize_account_key(account_name)
    path = snapshot_dir / f"{key}.jsonl"
    when = datetime.now(timezone.utc) - timedelta(days=age_days)
    record = {
        "account_name": account_name,
        "account_key": key,
        "saved_at": _iso_utc(when),
        "findings": findings or {
            "facility_count": 210,
            "trigger_events": [
                {"claim": "Memphis DC opening Q2", "source_url": "https://x/1"},
            ],
            "board_initiatives": [
                {"title": "Inventory accuracy push", "source_url": "https://x/2"},
            ],
            "competitor_signals": [],
            "dc_intel": [],
            "research_gaps": [],
        },
    }
    path.write_text(json.dumps(record) + "\n")
    return path


def _seed_session_row(sm, *, session_id, account_name, rep_id, thread_ts,
                     channel="D_CHAN_1", status="active",
                     normalized_request=None):
    db = sm()
    try:
        row = DBSession(
            id=session_id,
            account_name=account_name,
            rep_id=rep_id,
            channel_id=channel,
            status=status,
            normalized_request=normalized_request,
        )
        row.thread_ts = thread_ts
        db.add(row)
        db.commit()
    finally:
        db.close()


def _seed_company_research(sm, *, session_id, account_name, **overrides):
    db = sm()
    try:
        cr = CompanyResearch(
            id=overrides.get("id", f"cr-{session_id}"),
            session_id=session_id,
            account_name=account_name,
            facility_count=overrides.get("facility_count", 87),
            facility_count_note=overrides.get("facility_count_note", "from 10-K"),
            board_initiatives=overrides.get("board_initiatives", []),
            trigger_events=overrides.get("trigger_events", []),
            automation_vendors=overrides.get("automation_vendors", []),
            research_gaps=overrides.get("research_gaps", []),
            raw_research_text=overrides.get("raw_research_text", "raw"),
        )
        db.add(cr)
        db.commit()
    finally:
        db.close()


def _seed_persona(sm, *, session_id, first_name="Sarah", last_name="Chen",
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


def _collect_action_ids(say_mock):
    ids = set()
    for call in say_mock.call_args_list:
        blocks = call.kwargs.get("blocks") or []
        for b in blocks:
            if b.get("type") == "actions":
                for el in b.get("elements", []):
                    aid = el.get("action_id")
                    if aid:
                        ids.add(aid)
            if isinstance(b.get("accessory"), dict):
                aid = b["accessory"].get("action_id")
                if aid:
                    ids.add(aid)
    return ids


# ---------------------------------------------------------------------------
# 1. First-time account: intent card → click → research → brief
# ---------------------------------------------------------------------------


def test_e2e_first_time_account(mocker, monkeypatch, patched_db, tmp_path):
    """Spec §3 canonical flow: cold-start ambiguous account.

    DM "Volvo" → intent-capture card posted (with disambig + 4 intent
    buttons) → click `Volvo Group` (intent_disambig) + `Outbound`
    (intent_type) → research runs → brief lands with suggested-questions
    block.
    """
    from src.config import settings
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setattr(settings, "EXA_API_KEY", "exa-test")

    # 1a. Ambiguity check returns Volvo options (mock httpx in intent_capture).
    volvo_options = json.dumps([
        {"label": "Volvo Group", "distinguisher": "Trucks, buses, engines"},
        {"label": "Volvo Cars", "distinguisher": "Passenger cars, Geely-owned"},
    ])
    mocker.patch(
        "src.handlers.intent_capture.httpx.post",
        return_value=_make_llm_response(volvo_options),
    )

    # 1b. No prior snapshot → diff front-door short-circuit not triggered.
    # tmp_path is empty.

    # 1c. Findings pipeline — stub Exa + OpenAI inside findings_builder.
    fake_exa_result = [{
        "title": "Volvo Group inventory press release",
        "url": "https://volvo.example/news",
        "snippet": "Volvo Group announced new warehouse automation initiatives.",
        "published_date": "2026-02-01",
    }]
    mock_exa = MagicMock()
    mock_exa.search.return_value = fake_exa_result
    mocker.patch(
        "src.research.findings_builder.ExaSearchClient",
        return_value=mock_exa,
    )

    captured = {}
    def _openai_create(**kwargs):
        captured["kwargs"] = kwargs
        return _make_openai_response(json.dumps({
            "trigger_events": [
                {"claim": "Memphis DC opening Q2",
                 "source_url": "https://volvo.example/news"},
            ],
            "competitor_signals": [],
            "dc_intel": [],
            "board_initiatives": [],
            "research_gaps": [],
        }))

    openai_inst = MagicMock()
    openai_inst.chat.completions.create = MagicMock(side_effect=_openai_create)
    mocker.patch(
        "src.research.findings_builder.OpenAI", return_value=openai_inst
    )

    # Suggested questions — stub the agent return for determinism.
    mocker.patch(
        "src.research.runner.generate_suggested_questions",
        return_value=[
            "Who owns Memphis DC ramp?",
            "Lead with the inventory accuracy initiative?",
            "Best entry point at Volvo Group?",
        ],
    )
    mocker.patch("src.research.runner.is_fallback", return_value=False)

    # ----- DM "Volvo" -----
    from src.handlers.dm_research import handle_research_dm

    say = MagicMock(return_value={"ts": "1700010000.1000"})
    client = _make_client()

    handle_research_dm(
        message={
            "text": "Volvo",
            "user": "U_REP_1",
            "ts": "1700010000.1000",
            "channel": "D_CHAN_1",
        },
        say=say,
        client=client,
    )

    # Intent card should be on screen — disambig + 4 intent buttons.
    posted_ids = _collect_action_ids(say)
    assert any(aid.startswith("intent_type_") for aid in posted_ids)
    assert "intent_disambig" in posted_ids

    # Pull the session_id off the intent-card value payload.
    session_id = None
    for call in say.call_args_list:
        for b in (call.kwargs.get("blocks") or []):
            if b.get("type") == "actions":
                for el in b.get("elements", []):
                    if (el.get("action_id") or "").startswith("intent_type_"):
                        session_id = el.get("value", "").split("::", 1)[0]
                        break
        if session_id:
            break
    assert session_id, "intent_type button never rendered with a session_id"

    # ----- Click `Volvo Group` (intent_disambig) -----
    from src.main import _v1_action_intent_disambig, _v1_action_intent_type

    _v1_action_intent_disambig(
        ack=MagicMock(),
        body={
            "actions": [{"value": f"{session_id}::Volvo Group"}],
            "user": {"id": "U_REP_1"},
            "channel": {"id": "D_CHAN_1"},
            "message": {"ts": "1700010000.2000"},
        },
        say=say,
        client=client,
    )

    # ----- Click `Outbound` (intent_type) → research runs -----
    _v1_action_intent_type(
        ack=MagicMock(),
        body={
            "actions": [{"value": f"{session_id}::outbound"}],
            "user": {"id": "U_REP_1"},
            "channel": {"id": "D_CHAN_1"},
            "message": {"ts": "1700010000.3000"},
        },
        say=say,
        client=client,
    )

    # Brief was posted: suggested_question action block must appear.
    final_ids = _collect_action_ids(say)
    assert any(aid.startswith("suggested_question_") for aid in final_ids)

    # Intent reached the prompt: outbound emphasis stanza in system msg.
    assert "kwargs" in captured
    sys_content = "".join(
        m.get("content", "")
        for m in (captured["kwargs"].get("messages") or [])
        if m.get("role") == "system"
    )
    assert "EMPHASIS" in sys_content
    assert "cold-open" in sys_content.lower() or "outbound" in sys_content.lower()

    # And the disambiguation pinned to Volvo Group reached the prompt too.
    assert "Volvo Group" in sys_content

    # Persisted Session row carries intent_type=outbound + disambiguation.
    db = patched_db()
    try:
        row = db.query(DBSession).filter(DBSession.id == session_id).first()
    finally:
        db.close()
    assert (row.normalized_request or {}).get("intent_type") == "outbound"
    assert (row.normalized_request or {}).get("disambiguation") == "Volvo Group"


# ---------------------------------------------------------------------------
# 2. Repeat account: fresh snapshot → diff front-door → dig into new
# ---------------------------------------------------------------------------


def test_e2e_repeat_account_diff_front_door(mocker, patched_db, tmp_path):
    """Fresh snapshot exists → diff front-door card shown, no research
    runs. Clicking `Dig into what's new` posts the prior brief without
    any Exa/Apollo/HubSpot calls.
    """
    # Seed a 3-day-old snapshot for Walmart on disk.
    _seed_snapshot_file(tmp_path, "Walmart", age_days=3)

    # Trap external clients — `dig_into_new` must not touch them.
    raisers = {
        "src.research.clients_factory.get_apollo_client": "Apollo",
        "src.research.clients_factory.get_hubspot_account_client": "HubSpot",
        "src.research.clients_factory.get_hubspot_contact_client": "HubSpot",
        "src.research.findings_builder.ExaSearchClient": "Exa",
    }
    for path, name in raisers.items():
        mocker.patch(path, side_effect=AssertionError(f"{name} must not be called"))

    # Also guard research runner symbols so we know research never fired.
    mock_run_research = mocker.patch("src.handlers.dm_research.run_account_research")

    # ----- DM Walmart -----
    from src.handlers.dm_research import handle_research_dm

    say = MagicMock(return_value={"ts": "1700020000.1000"})
    client = _make_client()

    handle_research_dm(
        message={
            "text": "Walmart",
            "user": "U_REP_2",
            "ts": "1700020000.1000",
            "channel": "D_CHAN_1",
        },
        say=say,
        client=client,
    )

    # Diff front-door buttons on screen, research NOT fired.
    posted_ids = _collect_action_ids(say)
    assert {"re_research", "dig_into_new", "ask_specific"} <= posted_ids
    mock_run_research.assert_not_called()

    # Find the session_id off the diff-card button value.
    session_id = None
    for call in say.call_args_list:
        for b in (call.kwargs.get("blocks") or []):
            if b.get("type") == "actions":
                for el in b.get("elements", []):
                    if el.get("action_id") == "dig_into_new":
                        session_id = el.get("value")
                        break
        if session_id:
            break
    assert session_id

    # Seed a prior CompanyResearch row and link it to the new session row.
    db = patched_db()
    try:
        prior = CompanyResearch(
            id="cr-prior-walmart",
            session_id="sess-old",
            account_name="Walmart",
            facility_count=210,
            board_initiatives=[],
            trigger_events=[{"description": "Memphis DC", "source": "x"}],
            automation_vendors=[],
            research_gaps=[],
            raw_research_text="",
        )
        db.add(prior)
        # Link the new session.
        row = db.query(DBSession).filter(DBSession.id == session_id).first()
        if row is not None:
            normalized = dict(row.normalized_request or {})
            normalized["prior_company_research_id"] = "cr-prior-walmart"
            row.normalized_request = normalized
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(row, "normalized_request")
        db.commit()
    finally:
        db.close()

    # ----- Click `Dig into what's new` -----
    from src.main import _v1_action_dig_into_new

    _v1_action_dig_into_new(
        ack=MagicMock(),
        body={
            "actions": [{"value": session_id}],
            "user": {"id": "U_REP_2"},
            "channel": {"id": "D_CHAN_1"},
            "message": {"ts": "1700020000.2000"},
        },
        say=say,
        client=client,
    )

    # Prior brief was posted (text referencing Walmart somewhere).
    combined = ""
    for call in say.call_args_list:
        combined += call.kwargs.get("text", "") + " "
        for b in (call.kwargs.get("blocks") or []):
            if isinstance(b.get("text"), dict):
                combined += b["text"].get("text", "") + " "
    assert "Walmart" in combined

    # Research still never ran.
    mock_run_research.assert_not_called()


# ---------------------------------------------------------------------------
# 3. Follow-up Q&A: thread reply with mention → grounded answer
# ---------------------------------------------------------------------------


def test_e2e_followup_question_after_brief(mocker, monkeypatch, patched_db):
    """After research completes, a thread reply `<@BOT> who first?` routes
    to handle_followup. The LLM call is mocked to return a string that
    cites a real persona; a followup_question WorkflowEvent is logged.
    """
    from src.config import settings
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-test")

    # Seed a complete research session.
    _seed_session_row(
        patched_db,
        session_id="sess-fu-1",
        account_name="Sysco",
        rep_id="U_REP_3",
        thread_ts="1700030000.1000",
    )
    _seed_company_research(patched_db, session_id="sess-fu-1", account_name="Sysco")
    _seed_persona(
        patched_db,
        session_id="sess-fu-1",
        first_name="Sarah",
        last_name="Chen",
        title="Director of CI",
    )

    # Mock the followup LLM at the httpx layer.
    mocker.patch(
        "src.agents.followup_agent.httpx.post",
        return_value=_make_llm_response(
            "Lead with Sarah Chen — she owns continuous improvement."
        ),
    )

    # Drive a thread reply through dm_research → handle_followup.
    mocker.patch(
        "src.handlers.dm_research.get_bot_user_id", return_value="UBOT123"
    )

    from src.handlers.dm_research import handle_research_dm

    say = MagicMock(return_value={"ts": "1700030000.5000"})
    client = _make_client()

    handle_research_dm(
        message={
            "text": "<@UBOT123> who first?",
            "user": "U_REP_3",
            "ts": "1700030000.4000",
            "thread_ts": "1700030000.1000",
            "channel": "D_CHAN_1",
        },
        say=say,
        client=client,
    )

    # Placeholder must have been posted.
    assert say.called

    # Final answer landed via chat_update.
    client.chat_update.assert_called()
    update_kwargs = client.chat_update.call_args.kwargs
    final_text = ""
    for b in (update_kwargs.get("blocks") or []):
        if b.get("type") == "section":
            final_text += b.get("text", {}).get("text", "")
    assert "Sarah Chen" in final_text

    # followup_question event is logged.
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
    assert payload.get("session_id") is None  # not in payload — column
    assert evts[0].session_id == "sess-fu-1"
    assert "latency_ms" in payload


# ---------------------------------------------------------------------------
# 4. Suggested-question click equivalent to typed mention
# ---------------------------------------------------------------------------


def test_e2e_suggested_question_click_equals_typed_question(
    mocker, monkeypatch, patched_db,
):
    """A `suggested_question` button click and a typed `@`-mention with
    the same text both route into `handle_followup` with equivalent
    message shape (user, thread_ts, channel, text containing question).
    """
    from src.config import settings
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-test")

    _seed_session_row(
        patched_db,
        session_id="sess-sq-1",
        account_name="Volvo Group",
        rep_id="U_REP_4",
        thread_ts="1700040000.1000",
    )
    _seed_company_research(
        patched_db, session_id="sess-sq-1", account_name="Volvo Group"
    )

    mocker.patch(
        "src.handlers.dm_research.get_bot_user_id", return_value="UBOT123"
    )

    # Capture every handle_followup invocation via the import seam used by
    # both call sites (dm_research and suggested_question_click).
    captured_msgs = []

    def _capture(message, say, client):
        captured_msgs.append(message)

    mocker.patch("src.handlers.dm_research.handle_followup", side_effect=_capture)
    mocker.patch(
        "src.handlers.suggested_question_click.handle_followup",
        side_effect=_capture,
    )

    question = "Who do I call about Memphis DC?"

    # ----- Typed @-mention path -----
    from src.handlers.dm_research import handle_research_dm

    handle_research_dm(
        message={
            "text": f"<@UBOT123> {question}",
            "user": "U_REP_4",
            "ts": "1700040000.5001",
            "thread_ts": "1700040000.1000",
            "channel": "D_CHAN_1",
        },
        say=MagicMock(return_value={"ts": "1700040000.5050"}),
        client=_make_client(),
    )

    # ----- Suggested-question click path -----
    from src.handlers.suggested_question_click import handle_suggested_question

    handle_suggested_question(
        ack=MagicMock(),
        body={
            "actions": [
                {"value": question, "action_id": "suggested_question_0"},
            ],
            "user": {"id": "U_REP_4"},
            "channel": {"id": "D_CHAN_1"},
            "message": {
                "ts": "1700040000.1000",
                "thread_ts": "1700040000.1000",
            },
        },
        client=_make_client(),
    )

    assert len(captured_msgs) == 2
    typed, clicked = captured_msgs

    # Equivalent shape: user, channel, thread_ts identical; question text
    # appears in both (typed-mention path keeps the `<@UBOT123>` token,
    # click path prepends a synthesized one — both end up with the
    # question substring).
    assert typed["user"] == clicked["user"] == "U_REP_4"
    assert typed["channel"] == clicked["channel"] == "D_CHAN_1"
    assert typed["thread_ts"] == clicked["thread_ts"] == "1700040000.1000"
    assert question in typed["text"]
    assert question in clicked["text"]

    # And the click logged a suggested_question_clicked event with the
    # exact question text (sanity check the click path's event shape).
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
    assert (evts[0].payload or {}).get("question_text") == question


# ---------------------------------------------------------------------------
# 5. Unauthorized user — silent + forbidden_followup logged
# ---------------------------------------------------------------------------


def test_e2e_unauthorized_user_silent(mocker, patched_db):
    """A different rep mentioning the bot in someone else's thread →
    no Slack post; `forbidden_followup` WorkflowEvent logged.
    """
    _seed_session_row(
        patched_db,
        session_id="sess-auth-1",
        account_name="Sysco",
        rep_id="U_REP_OWNER",
        thread_ts="1700050000.1000",
    )

    mocker.patch(
        "src.handlers.dm_research.get_bot_user_id", return_value="UBOT123"
    )
    mock_followup = mocker.patch("src.handlers.dm_research.handle_followup")
    mock_create = mocker.patch("src.handlers.dm_research.create_session")

    from src.handlers.dm_research import handle_research_dm

    say = MagicMock()
    client = _make_client()
    handle_research_dm(
        message={
            "text": "<@UBOT123> peek?",
            "user": "U_REP_INTRUDER",
            "ts": "1700050000.5000",
            "thread_ts": "1700050000.1000",
            "channel": "D_CHAN_1",
        },
        say=say,
        client=client,
    )

    # Silent — no Slack post, no follow-up handler.
    say.assert_not_called()
    mock_followup.assert_not_called()
    mock_create.assert_not_called()

    # forbidden_followup logged.
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
    assert evts[0].rep_id == "U_REP_INTRUDER"
    assert evts[0].session_id == "sess-auth-1"


# ---------------------------------------------------------------------------
# 6. Q&A path never touches Exa / Apollo / HubSpot
# ---------------------------------------------------------------------------


def test_e2e_no_fresh_retrieval_anywhere_in_qa_path(
    mocker, monkeypatch, patched_db,
):
    """Trap the external clients used by research; run the full Q&A flow;
    none of the traps must fire. Mirrors Phase-4 test #16's approach.
    """
    from src.config import settings
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-test")

    _seed_session_row(
        patched_db,
        session_id="sess-qa-1",
        account_name="Sysco",
        rep_id="U_REP_5",
        thread_ts="1700060000.1000",
    )
    _seed_company_research(
        patched_db, session_id="sess-qa-1", account_name="Sysco"
    )

    # Trap external clients with constructors that raise.
    def _trap(name):
        def _raise(*a, **kw):
            raise AssertionError(f"{name} must not be invoked in Q&A path")
        return _raise

    mocker.patch(
        "src.research.findings_builder.ExaSearchClient",
        side_effect=_trap("Exa"),
    )
    mocker.patch(
        "src.research.clients_factory.get_apollo_client",
        side_effect=_trap("Apollo"),
    )
    mocker.patch(
        "src.research.clients_factory.get_hubspot_account_client",
        side_effect=_trap("HubSpot account"),
    )
    mocker.patch(
        "src.research.clients_factory.get_hubspot_contact_client",
        side_effect=_trap("HubSpot contact"),
    )

    # Followup LLM returns a normal answer — Q&A is the ONLY allowed network.
    mocker.patch(
        "src.agents.followup_agent.httpx.post",
        return_value=_make_llm_response("Sysco answer."),
    )

    mocker.patch(
        "src.handlers.dm_research.get_bot_user_id", return_value="UBOT123"
    )

    from src.handlers.dm_research import handle_research_dm

    say = MagicMock(return_value={"ts": "1700060000.5050"})
    client = _make_client()
    handle_research_dm(
        message={
            "text": "<@UBOT123> what gaps are left?",
            "user": "U_REP_5",
            "ts": "1700060000.5000",
            "thread_ts": "1700060000.1000",
            "channel": "D_CHAN_1",
        },
        say=say,
        client=client,
    )

    # If we got here without a trap firing, the assertion holds. Verify
    # the answer actually shipped.
    client.chat_update.assert_called()


# ---------------------------------------------------------------------------
# 7. Intent (renewal) reaches the research prompt body
# ---------------------------------------------------------------------------


def test_e2e_research_passes_intent_to_agents(mocker, monkeypatch, patched_db):
    """Fire `intent_type::renewal` → research runs → captured OpenAI
    prompt contains the literal renewal-emphasis stanza from
    `findings_builder._INTENT_EMPHASIS`.
    """
    from src.config import settings
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setattr(settings, "EXA_API_KEY", "exa-test")

    # Seed a session ready to fire intent.
    _seed_session_row(
        patched_db,
        session_id="sess-intent-renewal",
        account_name="Sysco",
        rep_id="U_REP_6",
        thread_ts="1700070000.1000",
    )

    # Stub Exa so findings_builder reaches the OpenAI call.
    mock_exa = MagicMock()
    mock_exa.search.return_value = [{
        "title": "Sysco renewal earnings",
        "url": "https://sysco.example/q1",
        "snippet": "Sysco discussed renewal-relevant inventory accuracy work.",
        "published_date": "2026-01-15",
    }]
    mocker.patch(
        "src.research.findings_builder.ExaSearchClient",
        return_value=mock_exa,
    )

    # Capture the OpenAI call inside findings_builder.
    captured = {}
    def _create(**kwargs):
        captured["kwargs"] = kwargs
        return _make_openai_response(json.dumps({
            "trigger_events": [],
            "competitor_signals": [],
            "dc_intel": [],
            "board_initiatives": [],
            "research_gaps": [],
        }))

    openai_inst = MagicMock()
    openai_inst.chat.completions.create = MagicMock(side_effect=_create)
    mocker.patch(
        "src.research.findings_builder.OpenAI", return_value=openai_inst
    )

    # Stub suggested questions so they don't burn extra LLM time.
    mocker.patch(
        "src.research.runner.generate_suggested_questions",
        return_value=["q1", "q2", "q3"],
    )
    mocker.patch("src.research.runner.is_fallback", return_value=False)

    # Fire intent_type::renewal.
    from src.main import _v1_action_intent_type

    say = MagicMock(return_value={"ts": "1700070000.2000"})
    client = _make_client()

    _v1_action_intent_type(
        ack=MagicMock(),
        body={
            "actions": [{"value": "sess-intent-renewal::renewal"}],
            "user": {"id": "U_REP_6"},
            "channel": {"id": "D_CHAN_1"},
            "message": {"ts": "1700070000.1500"},
        },
        say=say,
        client=client,
    )

    # The renewal emphasis stanza (literal from findings_builder).
    assert "kwargs" in captured, "OpenAI was never called from runner"
    messages = captured["kwargs"].get("messages") or []
    sys_content = "".join(
        m.get("content", "")
        for m in messages
        if m.get("role") == "system"
    )

    # Direct sample from findings_builder._INTENT_EMPHASIS["renewal"].
    assert "expansion signals" in sys_content
    assert "risk indicators" in sys_content
    assert "existing-relationship" in sys_content
