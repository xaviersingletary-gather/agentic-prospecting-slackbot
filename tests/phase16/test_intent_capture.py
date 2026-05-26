"""Phase 3 (V1 Daily-Use) — Move 1: intent capture.

Covers spec §5 Move 1 + §7 Success Criteria 1-4.

In-memory sqlite + SessionLocal patching mirrors the Phase 1/2 pattern in
`tests/phase16/test_session_foundation.py` and `test_diff_front_door.py`.
External Slack/Exa/Apollo/HubSpot clients are mocked — no real API calls.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, Session as DBSession, WorkflowEvent


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
    """Patch every module that captured `SessionLocal` at import time.

    `src.db.session.SessionLocal` is the canonical attribute, but
    `src.main` does `from src.db.session import SessionLocal` at
    module load, binding the original (real-Postgres) sessionmaker
    into its own namespace. We patch both so handler code under test
    talks to our in-memory sqlite engine.
    """
    mocker.patch("src.db.session.SessionLocal", db_sessionmaker)
    mocker.patch("src.main.SessionLocal", db_sessionmaker)
    return db_sessionmaker


@pytest.fixture(autouse=True)
def _reset_intent_cache():
    """Clear the module-level ambiguity cache between tests."""
    from src.handlers import intent_capture
    intent_capture._reset_cache_for_tests()
    yield
    intent_capture._reset_cache_for_tests()


@pytest.fixture(autouse=True)
def _reset_sessions():
    from src.research import sessions
    sessions._reset_for_tests()
    yield
    sessions._reset_for_tests()


@pytest.fixture(autouse=True)
def _stub_snapshot_save(tmp_path, monkeypatch):
    """Phase 1/2 used the on-disk snapshot dir as a sentinel — make sure
    save_snapshot can't trip on disk I/O when these tests run research
    paths."""
    monkeypatch.setenv("ACCOUNT_SNAPSHOTS_DIR", str(tmp_path))


def _make_llm_response(content: str) -> MagicMock:
    """Build a mock httpx Response whose `.json()` returns the OpenRouter
    chat-completions shape."""
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status.return_value = None
    resp.json.return_value = {
        "choices": [{"message": {"content": content}}]
    }
    return resp


# ---------------------------------------------------------------------------
# is_account_ambiguous — LLM call + caching + failure modes
# ---------------------------------------------------------------------------


def test_ambiguity_check_returns_options_for_volvo(mocker, monkeypatch):
    """Two notable Volvos exist; the helper returns a 2-item list."""
    from src.config import settings
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-test")

    payload = json.dumps([
        {"label": "Volvo Group", "distinguisher": "Trucks, buses, engines, marine"},
        {"label": "Volvo Cars", "distinguisher": "Passenger cars, owned by Geely"},
    ])
    mock_post = mocker.patch(
        "src.handlers.intent_capture.httpx.post",
        return_value=_make_llm_response(payload),
    )

    from src.handlers.intent_capture import is_account_ambiguous

    result = is_account_ambiguous("Volvo")

    assert mock_post.call_count == 1
    assert result is not None
    assert len(result) == 2
    labels = [r["label"] for r in result]
    assert "Volvo Group" in labels
    assert "Volvo Cars" in labels


def test_ambiguity_check_returns_none_for_sysco(mocker, monkeypatch):
    """Single-company name — LLM returns `null`; helper returns None."""
    from src.config import settings
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-test")

    mocker.patch(
        "src.handlers.intent_capture.httpx.post",
        return_value=_make_llm_response("null"),
    )

    from src.handlers.intent_capture import is_account_ambiguous

    assert is_account_ambiguous("Sysco") is None


def test_ambiguity_check_caches_per_account(mocker, monkeypatch):
    """A second call with the same lowercased key MUST NOT hit httpx."""
    from src.config import settings
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-test")

    mock_post = mocker.patch(
        "src.handlers.intent_capture.httpx.post",
        return_value=_make_llm_response("null"),
    )

    from src.handlers.intent_capture import is_account_ambiguous

    is_account_ambiguous("Sysco")
    is_account_ambiguous("Sysco")
    is_account_ambiguous("sysco")  # case-insensitive

    assert mock_post.call_count == 1


def test_ambiguity_check_handles_llm_failure(mocker, monkeypatch, caplog):
    """LLM raises → helper returns None, logs by exception type."""
    from src.config import settings
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-test")

    mocker.patch(
        "src.handlers.intent_capture.httpx.post",
        side_effect=RuntimeError("boom"),
    )

    from src.handlers.intent_capture import is_account_ambiguous

    with caplog.at_level("WARNING"):
        result = is_account_ambiguous("Acme")

    assert result is None
    # log line carries the exception type name, not the message
    log_text = "\n".join(r.message for r in caplog.records)
    assert "RuntimeError" in log_text
    assert "boom" not in log_text  # str(e) must NOT leak


# ---------------------------------------------------------------------------
# intent_capture_card
# ---------------------------------------------------------------------------


def test_card_includes_disambiguation_when_options_present():
    from src.handlers.intent_capture import intent_capture_card

    options = [
        {"label": "Volvo Group", "distinguisher": "Trucks"},
        {"label": "Volvo Cars", "distinguisher": "Passenger cars"},
    ]
    blocks = intent_capture_card("Volvo", options, "sess-1")

    rendered = " ".join(
        b.get("text", {}).get("text", "")
        for b in blocks
        if isinstance(b.get("text"), dict)
    )
    assert "Volvo Group" in rendered
    assert "Volvo Cars" in rendered

    # Disambig buttons present
    action_ids = []
    for b in blocks:
        if b.get("type") == "actions":
            for el in b.get("elements", []):
                action_ids.append(el.get("action_id"))
        if b.get("type") == "section" and isinstance(b.get("accessory"), dict):
            action_ids.append(b["accessory"].get("action_id"))
    assert "intent_disambig" in action_ids


def test_card_omits_disambiguation_when_none():
    from src.handlers.intent_capture import intent_capture_card

    blocks = intent_capture_card("Sysco", None, "sess-2")

    rendered = " ".join(
        b.get("text", {}).get("text", "")
        for b in blocks
        if isinstance(b.get("text"), dict)
    ).lower()
    # No "Which Sysco?" header when unambiguous.
    assert "which sysco?" not in rendered

    # No disambig buttons.
    action_ids = []
    for b in blocks:
        if b.get("type") == "actions":
            for el in b.get("elements", []):
                action_ids.append(el.get("action_id"))
        if b.get("type") == "section" and isinstance(b.get("accessory"), dict):
            action_ids.append(b["accessory"].get("action_id"))
    assert "intent_disambig" not in action_ids


def test_card_always_includes_four_intent_buttons():
    from src.handlers.intent_capture import intent_capture_card

    # Without disambig
    blocks = intent_capture_card("Sysco", None, "sess-A")
    intent_buttons = _collect_intent_buttons(blocks)
    assert len(intent_buttons) == 4
    values = {b.get("value", "").split("::", 1)[-1] for b in intent_buttons}
    assert values == {"prospecting", "meeting_prep", "asset_building", "general_research"}

    # With disambig
    options = [{"label": "Volvo Group", "distinguisher": "Trucks"}]
    blocks2 = intent_capture_card("Volvo", options, "sess-B")
    intent_buttons2 = _collect_intent_buttons(blocks2)
    assert len(intent_buttons2) == 4


def _collect_intent_buttons(blocks):
    out = []
    for b in blocks:
        if b.get("type") == "actions":
            for el in b.get("elements", []):
                if (el.get("action_id") or "").startswith("intent_type_"):
                    out.append(el)
    return out


# ---------------------------------------------------------------------------
# handle_research_dm — posts intent card; does NOT fire research
# ---------------------------------------------------------------------------


def test_handle_research_dm_posts_intent_card_when_no_snapshot(mocker, patched_db):
    """Spec §7 success criterion 1: cold start → intent card posted,
    `run_account_research` NOT called."""
    mocker.patch(
        "src.handlers.dm_research.should_show_diff_front_door",
        return_value=None,
    )
    mocker.patch(
        "src.handlers.dm_research.is_account_ambiguous",
        return_value=None,
    )
    mock_run = mocker.patch("src.handlers.dm_research.run_account_research")

    from src.handlers.dm_research import handle_research_dm

    say = MagicMock(return_value={"ts": "1700001111.0001"})
    client = MagicMock()
    handle_research_dm(
        message={
            "text": "Sysco",
            "user": "U_REP_1",
            "ts": "1700001111.0000",
            "channel": "D_CHAN_1",
        },
        say=say,
        client=client,
    )

    mock_run.assert_not_called()

    # Confirm the intent card landed: scan say() calls for the four
    # intent_type action_ids.
    posted_action_ids = set()
    for call in say.call_args_list:
        blocks = call.kwargs.get("blocks") or []
        for b in blocks:
            if b.get("type") == "actions":
                for el in b.get("elements", []):
                    if (el.get("action_id") or "").startswith("intent_type_"):
                        posted_action_ids.add(el.get("value", "").split("::", 1)[-1])
    assert {"prospecting", "meeting_prep", "asset_building", "general_research"} <= posted_action_ids


def test_handle_research_dm_does_not_run_research_until_intent_submitted(
    mocker, patched_db,
):
    """Two DMs in a row: still zero `run_account_research` calls because
    no intent button has been clicked yet."""
    mocker.patch(
        "src.handlers.dm_research.should_show_diff_front_door",
        return_value=None,
    )
    mocker.patch(
        "src.handlers.dm_research.is_account_ambiguous",
        return_value=None,
    )
    mock_run = mocker.patch("src.handlers.dm_research.run_account_research")

    from src.handlers.dm_research import handle_research_dm

    say = MagicMock(return_value={"ts": "1700001112.0001"})
    client = MagicMock()
    for ts in ("1700001112.0001", "1700001112.0002"):
        handle_research_dm(
            message={
                "text": "Sysco",
                "user": "U_REP_1",
                "ts": ts,
                "channel": "D_CHAN_1",
            },
            say=say,
            client=client,
        )

    mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# submit_intent action handler
# ---------------------------------------------------------------------------


def _seed_db_session(sm, *, session_id: str, account_name: str, rep_id: str) -> None:
    db = sm()
    try:
        row = DBSession(
            id=session_id,
            account_name=account_name,
            rep_id=rep_id,
            channel_id="D_CHAN_1",
        )
        row.thread_ts = "1700002000.0001"
        db.add(row)
        db.commit()
    finally:
        db.close()


def test_submit_intent_persists_to_normalized_request(mocker, patched_db):
    """Clicking an intent button writes `intent_type` to
    `Session.normalized_request`."""
    mocker.patch("src.main._v1_run_account_research")
    _seed_db_session(patched_db, session_id="sess-i1", account_name="Sysco", rep_id="U1")

    from src.main import _v1_action_intent_type

    ack = MagicMock()
    say = MagicMock()
    client = MagicMock()
    body = {
        "actions": [{"value": "sess-i1::prospecting"}],
        "user": {"id": "U1"},
        "message": {"ts": "1700002000.0001"},
        "channel": {"id": "D_CHAN_1"},
    }
    _v1_action_intent_type(ack=ack, body=body, say=say, client=client)

    ack.assert_called_once()

    db = patched_db()
    try:
        row = db.query(DBSession).filter(DBSession.id == "sess-i1").first()
    finally:
        db.close()
    assert row is not None
    assert (row.normalized_request or {}).get("intent_type") == "prospecting"


def test_submit_intent_logs_event(mocker, patched_db):
    """A `WorkflowEvent(event_type="intent_captured", ...)` row lands."""
    mocker.patch("src.main._v1_run_account_research")
    _seed_db_session(patched_db, session_id="sess-i2", account_name="Sysco", rep_id="U1")

    # `log_event` opens its own DB session via `get_session()` — patch
    # that helper to use our in-memory engine too.
    from contextlib import contextmanager

    @contextmanager
    def _ctx():
        db = patched_db()
        try:
            yield db
        finally:
            db.close()

    mocker.patch("src.main.get_session", _ctx)

    from src.main import _v1_action_intent_type

    body = {
        "actions": [{"value": "sess-i2::meeting_prep"}],
        "user": {"id": "U1"},
        "message": {"ts": "1700002000.0001"},
        "channel": {"id": "D_CHAN_1"},
    }
    _v1_action_intent_type(ack=MagicMock(), body=body, say=MagicMock(), client=MagicMock())

    db = patched_db()
    try:
        evts = (
            db.query(WorkflowEvent)
            .filter(WorkflowEvent.event_type == "intent_captured")
            .all()
        )
    finally:
        db.close()
    assert len(evts) == 1
    payload = evts[0].payload or {}
    assert payload.get("intent_type") == "meeting_prep"


def test_submit_intent_fires_run_v1_research_with_intent(mocker, patched_db):
    """Phase 5 cutover — the intent-type handler now drives the new
    `run_v1_research_sync` pipeline. The mocked call receives the
    captured intent + a ResearchSession carrying account_name."""
    mock_run = mocker.patch(
        "src.research.agents.runner_v1.run_v1_research_sync"
    )
    _seed_db_session(patched_db, session_id="sess-i3", account_name="Sysco", rep_id="U1")

    from src.main import _v1_action_intent_type

    body = {
        "actions": [{"value": "sess-i3::asset_building"}],
        "user": {"id": "U1"},
        "message": {"ts": "1700002000.0001"},
        "channel": {"id": "D_CHAN_1"},
    }
    _v1_action_intent_type(
        ack=MagicMock(), body=body, say=MagicMock(), client=MagicMock()
    )

    mock_run.assert_called_once()
    kwargs = mock_run.call_args.kwargs
    assert kwargs["intent"] == "asset_building"
    assert kwargs["thread_ts"] == "1700002000.0001"
    assert kwargs["channel_id"] == "D_CHAN_1"
    assert kwargs["rep_id"] == "U1"
    sess = kwargs["session"]
    assert sess is not None
    assert sess.account_name == "Sysco"
    assert (sess.normalized_request or {}).get("intent_type") == "asset_building"


# ---------------------------------------------------------------------------
# Intent emphasis lands in the research-agent prompt
# ---------------------------------------------------------------------------


def test_research_agent_prompt_includes_intent_emphasis(mocker, monkeypatch):
    """`run_account_research` with `intent_type="prospecting"` produces a
    research-agent prompt that contains the outbound emphasis line.

    We patch the OpenAI client used inside `findings_builder` (the LLM
    call site reached from `run_account_research`) and assert the
    captured `messages[0]` system content includes the emphasis stanza.
    """
    from src.config import settings
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setattr(settings, "EXA_API_KEY", "exa-test")

    # Stub Exa so we have at least one snippet — without this the
    # findings builder skips the LLM call entirely.
    fake_search_result = [{
        "title": "Sysco Foods Q1 Earnings",
        "url": "https://example.com/sysco-q1",
        "snippet": "Sysco discussed inventory accuracy on Q1 earnings.",
        "published_date": "2026-01-15",
    }]
    mock_exa = MagicMock()
    mock_exa.search.return_value = fake_search_result
    mocker.patch(
        "src.research.findings_builder.ExaSearchClient",
        return_value=mock_exa,
    )

    # Capture the LLM call. The OpenAI client is constructed inside
    # `_call_openrouter`; we stub the class to return a mock whose
    # `chat.completions.create` records the kwargs.
    captured = {}

    def _create(**kwargs):
        captured["kwargs"] = kwargs
        # Return an OpenAI-shaped response with a valid (empty) JSON body
        # so `_sanitize_findings` doesn't crash.
        msg = MagicMock()
        msg.content = json.dumps({
            "trigger_events": [],
            "competitor_signals": [],
            "dc_intel": [],
            "board_initiatives": [],
            "research_gaps": [],
        })
        choice = MagicMock()
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]
        return resp

    mock_openai_instance = MagicMock()
    mock_openai_instance.chat.completions.create = MagicMock(side_effect=_create)
    mocker.patch(
        "src.research.findings_builder.OpenAI",
        return_value=mock_openai_instance,
    )

    # Build a ResearchSession with intent_type="prospecting" and fire.
    from src.research.sessions import ResearchSession
    from src.research.runner import run_account_research

    sess = ResearchSession(
        session_id="sess-prompt-1",
        rep_id="U1",
        account_name="Sysco",
        personas=[],
    )
    sess.normalized_request = {"intent_type": "prospecting"}

    posts = []
    def _say(**kw):
        posts.append(kw)

    run_account_research(sess, _say)

    # The OpenAI call should have captured the prompt.
    assert "kwargs" in captured, "OpenAI.chat.completions.create was not invoked"
    messages = captured["kwargs"].get("messages") or []
    system_content = ""
    for m in messages:
        if m.get("role") == "system":
            system_content += m.get("content", "")
    assert "EMPHASIS" in system_content
    assert "trigger events" in system_content.lower() or "cold-open" in system_content.lower()
