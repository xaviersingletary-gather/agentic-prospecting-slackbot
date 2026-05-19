"""Phase 4 (V1 Daily-Use) — Move 2 agent tests.

Covers TDD plan Phase 4 (17 tests). Spec §5 Move 2 + §7 success criteria
5-12. No Slack handler integration here — pure functions only.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_company_research(**overrides):
    base = dict(
        account_name="Sysco",
        facility_count=87,
        facility_count_note="from 2025 10-K",
        board_initiatives=[
            {"title": "Inventory accuracy initiative", "source": "https://example.com/q1"},
        ],
        trigger_events=[
            {"description": "Memphis DC opening Q3 2026", "source": "https://example.com/memphis"},
        ],
        automation_vendors=[
            {"vendor_name": "Manhattan", "category": "WMS"},
        ],
        research_gaps=["No public roadmap for autonomous tech"],
        raw_research_text="Sysco is a US food distributor. " * 100,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _make_persona(persona_id="p1", **overrides):
    base = dict(
        id=persona_id,
        first_name="Sarah",
        last_name="Chen",
        title="Director of Continuous Improvement",
        persona_type="TDM",
        seniority="Director",
        priority_score="High",
        value_driver="Inventory accuracy",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _make_contact_research(persona_id="p1", **overrides):
    base = dict(
        persona_id=persona_id,
        current_role_tenure="2 years 3 months",
        prior_roles=[{"title": "CI Manager", "company": "Acme", "duration": "3y"}],
        recent_linkedin=[{"type": "post", "content": "Memphis ramp-up", "date": "2026-05-01"}],
        speaking_activity="Spoke at MODX 2025",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _make_session(account_name="Sysco"):
    return SimpleNamespace(account_name=account_name, id="sess-1", rep_id="U1")


def _make_llm_response(content: str) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"choices": [{"message": {"content": content}}]}
    return resp


# ---------------------------------------------------------------------------
# build_followup_context
# ---------------------------------------------------------------------------


def test_context_includes_account_name():
    from src.agents.followup_agent import build_followup_context

    ctx = build_followup_context(
        session=_make_session("Volvo Group"),
        company_research=_make_company_research(),
        personas=[_make_persona()],
        contact_researches=[],
        thread_history=[],
        question="Who first?",
    )
    assert "Volvo Group" in ctx


def test_context_includes_facility_count():
    from src.agents.followup_agent import build_followup_context

    ctx = build_followup_context(
        session=_make_session(),
        company_research=_make_company_research(facility_count=87),
        personas=[],
        contact_researches=[],
        thread_history=[],
        question="How many DCs?",
    )
    assert "87" in ctx


def test_context_includes_persona_names_and_titles():
    from src.agents.followup_agent import build_followup_context

    personas = [
        _make_persona("p1", first_name="Sarah", last_name="Chen", title="Director of CI"),
        _make_persona("p2", first_name="Mark", last_name="Rivera", title="VP Operations"),
    ]
    ctx = build_followup_context(
        session=_make_session(),
        company_research=_make_company_research(),
        personas=personas,
        contact_researches=[],
        thread_history=[],
        question="Who?",
    )
    assert "Sarah Chen" in ctx
    assert "Director of CI" in ctx
    assert "Mark Rivera" in ctx
    assert "VP Operations" in ctx


def test_context_includes_flagged_contact_research():
    from src.agents.followup_agent import build_followup_context

    persona = _make_persona("p1")
    cr = _make_contact_research(
        persona_id="p1",
        current_role_tenure="2 years 3 months",
        speaking_activity="Spoke at MODX 2025",
    )
    ctx = build_followup_context(
        session=_make_session(),
        company_research=_make_company_research(),
        personas=[persona],
        contact_researches=[cr],
        thread_history=[],
        question="Tell me about Sarah.",
    )
    assert "2 years 3 months" in ctx
    assert "MODX 2025" in ctx


def test_context_truncates_raw_research_to_12k():
    from src.agents.followup_agent import build_followup_context

    # 50k characters of raw research → must be cut to ~12k in the context
    # for the company section, with the truncation marker present.
    huge_text = "x" * 50_000
    cr = _make_company_research(raw_research_text=huge_text)
    ctx = build_followup_context(
        session=_make_session(),
        company_research=cr,
        personas=[],
        contact_researches=[],
        thread_history=[],
        question="?",
    )
    # The raw text block must not contain a 13k-char run of "x".
    assert "x" * 12_001 not in ctx
    assert "…truncated…" in ctx


def test_context_includes_thread_history_oldest_first():
    from src.agents.followup_agent import build_followup_context

    history = [
        {"user": "U1", "text": "first message", "ts": "1.0"},
        {"user": "BOT", "text": "second message", "ts": "2.0"},
        {"user": "U1", "text": "third message", "ts": "3.0"},
    ]
    ctx = build_followup_context(
        session=_make_session(),
        company_research=_make_company_research(),
        personas=[],
        contact_researches=[],
        thread_history=history,
        question="?",
    )
    first_idx = ctx.index("first message")
    second_idx = ctx.index("second message")
    third_idx = ctx.index("third message")
    assert first_idx < second_idx < third_idx


def test_context_caps_total_size_at_28k():
    from src.agents.followup_agent import build_followup_context

    huge_text = "y" * 60_000
    cr = _make_company_research(raw_research_text=huge_text)

    # Pad with a lot of personas to push total context up.
    personas = [_make_persona(f"p{i}", first_name=f"Person{i}", last_name="X") for i in range(50)]
    ctx = build_followup_context(
        session=_make_session(),
        company_research=cr,
        personas=personas,
        contact_researches=[],
        thread_history=[],
        question="?",
    )
    assert len(ctx) <= 28_000


def test_context_includes_current_question_verbatim():
    from src.agents.followup_agent import build_followup_context

    question = "Which contact should I hit first now that Q1 earnings dropped?"
    ctx = build_followup_context(
        session=_make_session(),
        company_research=_make_company_research(),
        personas=[],
        contact_researches=[],
        thread_history=[],
        question=question,
    )
    assert question in ctx


def test_context_handles_no_personas():
    from src.agents.followup_agent import build_followup_context

    ctx = build_followup_context(
        session=_make_session(),
        company_research=_make_company_research(),
        personas=[],
        contact_researches=[],
        thread_history=[],
        question="?",
    )
    # Must not crash, and must mention an empty-persona marker.
    assert "Contacts" in ctx
    assert "no personas" in ctx.lower()


def test_context_handles_no_thread_history():
    from src.agents.followup_agent import build_followup_context

    ctx = build_followup_context(
        session=_make_session(),
        company_research=_make_company_research(),
        personas=[],
        contact_researches=[],
        thread_history=[],
        question="?",
    )
    assert "first follow-up" in ctx


# ---------------------------------------------------------------------------
# answer_followup
# ---------------------------------------------------------------------------


def test_answer_returns_llm_content_on_success(mocker, monkeypatch):
    from src.config import settings

    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-test")
    mocker.patch(
        "src.agents.followup_agent.httpx.post",
        return_value=_make_llm_response("Sarah Chen is the top priority."),
    )

    from src.agents.followup_agent import answer_followup

    result = answer_followup("context blob")
    assert result == "Sarah Chen is the top priority."


def test_answer_handles_timeout(mocker, monkeypatch):
    from src.config import settings

    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-test")
    mocker.patch(
        "src.agents.followup_agent.httpx.post",
        side_effect=httpx.TimeoutException("slow"),
    )

    from src.agents.followup_agent import _FALLBACK_ANSWER, answer_followup

    assert answer_followup("ctx") == _FALLBACK_ANSWER


def test_answer_handles_non_2xx(mocker, monkeypatch):
    from src.config import settings

    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-test")

    resp = MagicMock()
    resp.status_code = 500
    resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "500", request=MagicMock(), response=resp,
    )
    mocker.patch("src.agents.followup_agent.httpx.post", return_value=resp)

    from src.agents.followup_agent import _FALLBACK_ANSWER, answer_followup

    assert answer_followup("ctx") == _FALLBACK_ANSWER


def test_answer_handles_malformed_json(mocker, monkeypatch):
    from src.config import settings

    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-test")

    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status.return_value = None
    resp.json.side_effect = ValueError("not json")
    mocker.patch("src.agents.followup_agent.httpx.post", return_value=resp)

    from src.agents.followup_agent import _FALLBACK_ANSWER, answer_followup

    assert answer_followup("ctx") == _FALLBACK_ANSWER


def test_answer_uses_configured_model(mocker, monkeypatch):
    from src.config import settings

    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setattr(settings, "OPENROUTER_MODEL", "anthropic/claude-test-x")

    mock_post = mocker.patch(
        "src.agents.followup_agent.httpx.post",
        return_value=_make_llm_response("answer"),
    )

    from src.agents.followup_agent import answer_followup

    answer_followup("ctx")

    assert mock_post.call_count == 1
    payload = mock_post.call_args.kwargs.get("json") or {}
    assert payload.get("model") == "anthropic/claude-test-x"


def test_answer_never_calls_exa_apollo_hubspot(mocker, monkeypatch):
    """answer_followup must be pure with respect to retrieval clients."""
    from src.config import settings

    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-test")

    # Patch the integration client constructors wherever the code might
    # import them. The Q&A path should never touch them — if it does,
    # the constructor blows up the test.
    targets = [
        "src.research.findings_builder.ExaSearchClient",
        "src.research.contacts.ApolloClient",
    ]
    for target in targets:
        try:
            mocker.patch(target, side_effect=AssertionError(f"{target} was invoked"))
        except (AttributeError, ModuleNotFoundError):
            # Module/attr may not exist in all builds — skip; the assertion
            # for THIS phase is that answer_followup completes without
            # raising, which still holds.
            pass

    # Patch HubSpot client where it could conceivably be imported.
    for hs_target in (
        "src.integrations.hubspot.HubSpotClient",
        "src.integrations.hubspot_client.HubSpotClient",
    ):
        try:
            mocker.patch(hs_target, side_effect=AssertionError(f"{hs_target} was invoked"))
        except (AttributeError, ModuleNotFoundError):
            pass

    mocker.patch(
        "src.agents.followup_agent.httpx.post",
        return_value=_make_llm_response("clean answer"),
    )

    from src.agents.followup_agent import answer_followup, build_followup_context

    ctx = build_followup_context(
        session=_make_session(),
        company_research=_make_company_research(),
        personas=[_make_persona()],
        contact_researches=[_make_contact_research()],
        thread_history=[{"user": "U1", "text": "earlier", "ts": "1.0"}],
        question="Who first?",
    )

    result = answer_followup(ctx)
    assert result == "clean answer"


def test_answer_does_not_log_api_keys(mocker, monkeypatch, caplog):
    """The OPENROUTER_API_KEY must never appear in any log line — root or
    module — for a successful call."""
    from src.config import settings

    sentinel = "sk-SENTINEL-DO-NOT-LEAK-9f3a1b"
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", sentinel)

    mocker.patch(
        "src.agents.followup_agent.httpx.post",
        return_value=_make_llm_response("ok"),
    )

    from src.agents.followup_agent import answer_followup

    with caplog.at_level(logging.DEBUG):
        answer_followup("ctx")

    # caplog captures records from all loggers (root + module).
    full_log = "\n".join(r.getMessage() for r in caplog.records)
    assert sentinel not in full_log
