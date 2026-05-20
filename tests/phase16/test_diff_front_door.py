"""Phase 2 (V1 Daily-Use) — Move 4: diff-first cold-start.

Covers spec §5 Move 4 + §7 Success Criteria 16-19.

In-memory sqlite + SessionLocal patching mirrors the Phase 1 pattern in
`tests/phase16/test_session_foundation.py`. External Slack/Exa/Apollo/
HubSpot clients are mocked — no real API calls.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, CompanyResearch, Session as DBSession


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
    mocker.patch("src.db.session.SessionLocal", db_sessionmaker)
    return db_sessionmaker


def _iso_utc(dt: datetime) -> str:
    """Match the `save_snapshot` format: `YYYY-MM-DDTHH:MM:SSZ`."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_snapshot(age_days: float, findings: dict = None) -> dict:
    when = datetime.now(timezone.utc) - timedelta(days=age_days)
    return {
        "account_name": "Walmart",
        "account_key": "walmart",
        "saved_at": _iso_utc(when),
        "findings": findings or {
            "facility_count": 210,
            "trigger_events": [
                {"claim": "Memphis DC opening Q2", "source_url": "https://x/1"},
            ],
            "board_initiatives": [
                {"title": "Inventory accuracy push", "source_url": "https://x/2"},
            ],
        },
    }


# ---------------------------------------------------------------------------
# should_show_diff_front_door
# ---------------------------------------------------------------------------


def test_should_show_returns_snapshot_when_fresh(mocker):
    from src.handlers import diff_front_door

    snap = _make_snapshot(age_days=3)
    mocker.patch(
        "src.handlers.diff_front_door.get_latest_snapshot",
        return_value=snap,
    )

    result = diff_front_door.should_show_diff_front_door("Walmart")
    assert result is snap


def test_should_show_returns_none_when_stale(mocker):
    from src.handlers import diff_front_door

    snap = _make_snapshot(age_days=20)
    mocker.patch(
        "src.handlers.diff_front_door.get_latest_snapshot",
        return_value=snap,
    )

    result = diff_front_door.should_show_diff_front_door("Walmart")
    assert result is None


def test_should_show_returns_none_when_no_snapshot(mocker):
    from src.handlers import diff_front_door

    mocker.patch(
        "src.handlers.diff_front_door.get_latest_snapshot",
        return_value=None,
    )

    result = diff_front_door.should_show_diff_front_door("Walmart")
    assert result is None


def test_freshness_threshold_respects_env_var(mocker, monkeypatch):
    from src.config import settings
    from src.handlers import diff_front_door

    monkeypatch.setattr(settings, "SNAPSHOT_FRESHNESS_DAYS", 7)

    snap = _make_snapshot(age_days=10)
    mocker.patch(
        "src.handlers.diff_front_door.get_latest_snapshot",
        return_value=snap,
    )

    result = diff_front_door.should_show_diff_front_door("Walmart")
    assert result is None


# ---------------------------------------------------------------------------
# diff_front_door_card
# ---------------------------------------------------------------------------


def test_card_includes_saved_at_date():
    from src.integrations.slack_blocks import diff_front_door_card

    # Use a fixed date so the human-readable rendering is predictable.
    snap = {
        "saved_at": "2026-05-15T18:42:11Z",
        "findings": {"facility_count": 100},
    }
    blocks = diff_front_door_card(snap, "Walmart", "sess-123")

    rendered = " ".join(
        b.get("text", {}).get("text", "")
        for b in blocks
        if isinstance(b.get("text"), dict)
    )
    # Should include a human-readable form of the saved_at month or year.
    assert "May" in rendered or "2026" in rendered


def test_card_has_three_action_buttons():
    from src.integrations.slack_blocks import diff_front_door_card

    snap = _make_snapshot(age_days=3)
    blocks = diff_front_door_card(snap, "Walmart", "sess-abc")

    actions_blocks = [b for b in blocks if b.get("type") == "actions"]
    assert len(actions_blocks) == 1
    action_ids = {el.get("action_id") for el in actions_blocks[0]["elements"]}
    assert {"re_research", "dig_into_new", "ask_specific"} <= action_ids


# ---------------------------------------------------------------------------
# handle_research_dm short-circuit / fall-through
# ---------------------------------------------------------------------------


def test_handle_research_dm_short_circuits_on_fresh_snapshot(mocker, patched_db):
    from src.handlers.dm_research import handle_research_dm
    from src.research import sessions as sessions_module

    sessions_module._reset_for_tests()

    fake_snapshot = _make_snapshot(age_days=2)
    mocker.patch(
        "src.handlers.dm_research.should_show_diff_front_door",
        return_value=fake_snapshot,
    )
    mock_run_research = mocker.patch(
        "src.handlers.dm_research.run_account_research"
    )
    mocker.patch(
        "src.handlers.dm_research.build_persona_select_blocks",
        return_value=[],
    )

    say = MagicMock(return_value={"ts": "1700000456.0001"})
    client = MagicMock()
    message = {
        "text": "Walmart",
        "user": "U_REP_1",
        "ts": "1700000123.4567",
        "channel": "D_CHAN_1",
    }

    handle_research_dm(message=message, say=say, client=client)

    # `run_account_research` must NOT have been called.
    mock_run_research.assert_not_called()
    # `say` must have been called with the diff front-door blocks at
    # least once. We check that an actions block with the three diff
    # button action_ids appears in some `blocks` kwarg.
    posted_action_ids = set()
    for call in say.call_args_list:
        blocks = call.kwargs.get("blocks") or []
        for block in blocks:
            if block.get("type") == "actions":
                for el in block.get("elements", []):
                    aid = el.get("action_id")
                    if aid:
                        posted_action_ids.add(aid)
    assert {"re_research", "dig_into_new", "ask_specific"} <= posted_action_ids


def test_handle_research_dm_runs_research_on_stale_snapshot(mocker, patched_db):
    """With Phase 3 (intent capture) layered on top of Phase 2, a stale /
    missing snapshot no longer immediately runs research — it posts the
    intent-capture card and waits for the rep to click an intent button.
    The Phase-2 contract (snapshot decision falls through) is still
    validated: `should_show_diff_front_door` returns None and the diff
    front-door card is NOT posted.
    """
    from src.handlers.dm_research import handle_research_dm
    from src.research import sessions as sessions_module

    sessions_module._reset_for_tests()

    mocker.patch(
        "src.handlers.dm_research.should_show_diff_front_door",
        return_value=None,
    )
    mocker.patch(
        "src.handlers.dm_research.is_account_ambiguous",
        return_value=None,
    )
    mock_run_research = mocker.patch(
        "src.handlers.dm_research.run_account_research"
    )
    mocker.patch(
        "src.handlers.dm_research.build_persona_select_blocks",
        return_value=[],
    )

    say = MagicMock(return_value={"ts": "1700000456.0002"})
    client = MagicMock()
    message = {
        "text": "Walmart",
        "user": "U_REP_1",
        "ts": "1700000124.4567",
        "channel": "D_CHAN_1",
    }

    handle_research_dm(message=message, say=say, client=client)

    # Research must NOT fire until the rep clicks an intent button.
    mock_run_research.assert_not_called()

    # Intent card must be posted; diff front-door buttons must NOT.
    posted_action_ids = set()
    for call in say.call_args_list:
        blocks = call.kwargs.get("blocks") or []
        for block in blocks:
            if block.get("type") == "actions":
                for el in block.get("elements", []):
                    aid = el.get("action_id")
                    if aid:
                        posted_action_ids.add(aid)
    assert any(aid.startswith("intent_type_") for aid in posted_action_ids)
    assert "re_research" not in posted_action_ids


# ---------------------------------------------------------------------------
# dig_into_new — no fresh retrieval (Success Criterion 18)
# ---------------------------------------------------------------------------


def test_dig_into_new_loads_prior_research_no_api_calls(mocker, patched_db):
    from src.handlers.diff_front_door import dig_into_new

    # Patch the external client constructors to raise on any call.
    def _raise_exa(*a, **kw):
        raise AssertionError("Exa client must not be constructed in dig_into_new")

    def _raise_apollo(*a, **kw):
        raise AssertionError("Apollo client must not be constructed in dig_into_new")

    def _raise_hubspot(*a, **kw):
        raise AssertionError("HubSpot client must not be constructed in dig_into_new")

    mocker.patch(
        "src.research.clients_factory.get_apollo_client",
        side_effect=_raise_apollo,
    )
    mocker.patch(
        "src.research.clients_factory.get_hubspot_account_client",
        side_effect=_raise_hubspot,
    )
    mocker.patch(
        "src.research.clients_factory.get_hubspot_contact_client",
        side_effect=_raise_hubspot,
    )

    # Seed: a prior CompanyResearch + a new Session that points back at it.
    sm = patched_db
    db = sm()
    try:
        prior_cr = CompanyResearch(
            id="cr-prior-1",
            session_id="sess-old",
            account_name="Walmart",
            facility_count=210,
            board_initiatives=[{"title": "Accuracy", "summary": "Push", "source": "x"}],
            trigger_events=[{"description": "Memphis DC", "source": "y"}],
            company_priorities=[],
            automation_vendors=[],
            research_gaps=[],
            documents_used=[],
            raw_research_text="",
        )
        db.add(prior_cr)
        new_session = DBSession(
            id="sess-new-1",
            account_name="Walmart",
            rep_id="U_REP_1",
            channel_id="D_CHAN_1",
            normalized_request={"prior_company_research_id": "cr-prior-1"},
        )
        new_session.thread_ts = "1700000888.0001"
        db.add(new_session)
        db.commit()
    finally:
        db.close()

    say = MagicMock()
    client = MagicMock()

    # Must not raise — and must not touch the patched clients.
    dig_into_new(
        session_id="sess-new-1",
        channel_id="D_CHAN_1",
        thread_ts="1700000888.0001",
        client=client,
        say=say,
    )

    # Some message must have landed (the brief or a fallback).
    assert say.called

    # And critically: posted blocks must reference the prior account.
    posted_text_parts = []
    for call in say.call_args_list:
        if "text" in call.kwargs:
            posted_text_parts.append(call.kwargs["text"])
        blocks = call.kwargs.get("blocks") or []
        for block in blocks:
            if isinstance(block.get("text"), dict):
                posted_text_parts.append(block["text"].get("text", ""))
    combined = " ".join(posted_text_parts)
    assert "Walmart" in combined


# ---------------------------------------------------------------------------
# re_research action handler
# ---------------------------------------------------------------------------


def test_re_research_button_starts_fresh_pipeline(mocker, patched_db):
    """Triggering `re_research` must call `run_account_research` once."""
    # Patch the runner symbol that the main module imports under an alias.
    mock_run = mocker.patch("src.main._v1_run_account_research")

    # Seed a session row so the handler can find an account_name.
    sm = patched_db
    db = sm()
    try:
        row = DBSession(
            id="sess-re-1",
            account_name="Walmart",
            rep_id="U_REP_1",
            channel_id="D_CHAN_1",
        )
        row.thread_ts = "1700001000.0001"
        db.add(row)
        db.commit()
    finally:
        db.close()

    # Import the action wrapper directly. `@app.action` decorators register
    # callbacks on the Bolt App but also return the underlying function.
    from src.main import _v1_action_re_research

    ack = MagicMock()
    say = MagicMock(return_value={"ts": "1700001000.0002"})
    client = MagicMock()

    body = {
        "actions": [{"value": "sess-re-1"}],
        "user": {"id": "U_REP_1"},
        "channel": {"id": "D_CHAN_1"},
        "message": {"ts": "1700001000.0001"},
    }

    _v1_action_re_research(ack=ack, body=body, say=say, client=client)

    ack.assert_called_once()
    mock_run.assert_called_once()
