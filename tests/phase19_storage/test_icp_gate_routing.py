"""Phase 2 — ICP gate routes between override card and intent capture.

  - check_icp_fit returns None → intent capture card is posted.
  - check_icp_fit returns reason → override card is posted, intent
    capture is NOT.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base


@pytest.fixture
def db_sessionmaker():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


@pytest.fixture
def common_patches(mocker, db_sessionmaker):
    mocker.patch("src.db.session.SessionLocal", db_sessionmaker)
    mocker.patch(
        "src.research.account_research_store.SessionLocal", db_sessionmaker
    )
    mocker.patch("src.main.SessionLocal", db_sessionmaker, create=True)
    mocker.patch(
        "src.handlers.dm_research.should_show_diff_front_door", return_value=None
    )
    mocker.patch(
        "src.handlers.dm_research.is_account_ambiguous", return_value=None
    )
    intent_capture_card = mocker.patch(
        "src.handlers.dm_research.intent_capture_card",
        return_value=[
            {"type": "section", "text": {"type": "mrkdwn", "text": "intent"}}
        ],
    )
    icp_override_card = mocker.patch(
        "src.handlers.dm_research.icp_override_card",
        return_value=[
            {"type": "section", "text": {"type": "mrkdwn", "text": "icp"}}
        ],
    )
    return {
        "intent_capture_card": intent_capture_card,
        "icp_override_card": icp_override_card,
    }


def _msg(text="Walmart"):
    return {"text": text, "user": "U_REP", "channel": "C1", "ts": "T_TS"}


def test_in_icp_account_skips_gate_and_posts_intent_card(
    mocker, common_patches
):
    mocker.patch(
        "src.handlers.dm_research.check_icp_fit", return_value=None
    )

    from src.handlers.dm_research import handle_research_dm
    say = MagicMock()
    handle_research_dm(_msg("Walmart"), say=say, client=MagicMock())

    common_patches["intent_capture_card"].assert_called_once()
    common_patches["icp_override_card"].assert_not_called()


def test_out_of_icp_account_posts_override_card_and_stops(
    mocker, common_patches
):
    mocker.patch(
        "src.handlers.dm_research.check_icp_fit",
        return_value={"reason": "pure SaaS"},
    )

    from src.handlers.dm_research import handle_research_dm
    say = MagicMock()
    handle_research_dm(_msg("Notion"), say=say, client=MagicMock())

    common_patches["icp_override_card"].assert_called_once()
    common_patches["intent_capture_card"].assert_not_called()
