"""Phase 21 — end-to-end runner_v1.

Mocks the dispatcher so we exercise: assemble → persist → render → post.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import AccountResearch, Base
from src.research.agents.contract import (
    AGENT_SECTIONS,
    AgentResult,
    Claim,
    SourceTag,
)
from src.research.sessions import ResearchSession


pytestmark = pytest.mark.asyncio


@pytest.fixture
def db_sm():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


@pytest.fixture
def patched_db(mocker, db_sm):
    mocker.patch("src.db.session.SessionLocal", db_sm)
    mocker.patch("src.research.account_research_store.SessionLocal", db_sm)
    return db_sm


def _ok_results():
    return [
        AgentResult(
            agent_name=name,
            section_title=AGENT_SECTIONS[name],
            claims=[Claim(text=f"finding for {name}", source_tag=SourceTag.NOT_FOUND)],
        )
        for name in AGENT_SECTIONS
    ]


async def test_runner_v1_persists_blob_and_posts_to_slack(mocker, patched_db):
    mocker.patch(
        "src.research.agents.runner_v1.dispatch",
        return_value=_ok_results(),
    )
    posts = []
    say = MagicMock(side_effect=lambda **kw: posts.append(kw))

    from src.research.agents.runner_v1 import run_v1_research

    sess = ResearchSession(
        session_id="s1",
        rep_id="U_REP",
        account_name="Walmart",
        personas=["technical_lead"],
    )
    row_id = await run_v1_research(
        session=sess,
        thread_ts="T_THREAD",
        channel_id="C1",
        rep_id="U_REP",
        intent="prospecting",
        post=say,
    )

    assert row_id is not None
    assert len(posts) >= 1
    # Each post carries blocks + a text fallback.
    for kw in posts:
        assert "blocks" in kw
        assert "text" in kw and "Walmart" in kw["text"]

    # The blob landed in the DB.
    db = patched_db()
    try:
        row = (
            db.query(AccountResearch)
            .filter(AccountResearch.thread_ts == "T_THREAD")
            .first()
        )
        assert row is not None
        assert row.account_name == "Walmart"
        assert row.intent == "prospecting"
        blob = row.research_blob
        assert blob and blob.get("schema_version") == 1
        assert len(blob["agents"]) == 10
    finally:
        db.close()


async def test_runner_v1_swallows_dispatch_failure_and_still_persists(
    mocker, patched_db
):
    mocker.patch(
        "src.research.agents.runner_v1.dispatch",
        side_effect=RuntimeError("dispatcher crash"),
    )
    say = MagicMock()
    from src.research.agents.runner_v1 import run_v1_research

    sess = ResearchSession(
        session_id="s2", rep_id="U", account_name="Acme", personas=[]
    )
    row_id = await run_v1_research(
        session=sess,
        thread_ts="T2",
        channel_id="C",
        rep_id="U",
        intent="prospecting",
        post=say,
    )

    # Even with dispatch failure, the row is created and Slack is posted
    # (the blob shows all 8 sections as ERROR placeholders).
    assert row_id is not None
    say.assert_called()


async def test_runner_v1_continues_post_loop_when_one_post_fails(
    mocker, patched_db, caplog
):
    # Force the renderer to produce 2+ chunks by giving each agent a
    # very long claim text.
    big_results = []
    long_text = "z" * 800
    for name in AGENT_SECTIONS:
        big_results.append(
            AgentResult(
                agent_name=name,
                section_title=AGENT_SECTIONS[name],
                claims=[Claim(text=long_text, source_tag=SourceTag.NOT_FOUND)] * 5,
            )
        )
    mocker.patch(
        "src.research.agents.runner_v1.dispatch", return_value=big_results
    )

    say = MagicMock(side_effect=RuntimeError("slack 500"))
    from src.research.agents.runner_v1 import run_v1_research

    sess = ResearchSession(
        session_id="s3", rep_id="U", account_name="X", personas=[]
    )
    with caplog.at_level("ERROR"):
        await run_v1_research(
            session=sess,
            thread_ts="T3",
            channel_id="C",
            rep_id="U",
            intent=None,
            post=say,
        )

    # Slack post failed on chunk 1; we should have stopped early — exactly
    # one post call attempted regardless of how many chunks the renderer
    # produced.
    assert say.call_count == 1
    log_text = "\n".join(r.message for r in caplog.records)
    assert "RuntimeError" in log_text
    assert "slack 500" not in log_text
