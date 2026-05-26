"""Phase 2 (May 26 V1 spec) — ICP sanity-check gate.

Mirrors the intent_capture test pattern: in-memory cache reset between
runs, httpx.post mocked, no real LLM calls.

Covers:
  - LLM says out_of_icp → helper returns {"reason": "..."}
  - LLM says in-ICP → None
  - LLM parse failure → None (fail-open)
  - LLM transport failure → None + log carries exception type only
  - Cache short-circuits repeat calls
  - Card builder sanitizes both account name and reason
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _reset_icp_cache():
    from src.handlers import icp_gate
    icp_gate._reset_cache_for_tests()
    yield
    icp_gate._reset_cache_for_tests()


def _resp(content: str) -> MagicMock:
    r = MagicMock()
    r.status_code = 200
    r.raise_for_status.return_value = None
    r.json.return_value = {"choices": [{"message": {"content": content}}]}
    return r


def test_icp_gate_returns_reason_when_llm_flags_account(mocker, monkeypatch):
    from src.config import settings
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-test")

    payload = json.dumps(
        {"out_of_icp": True, "reason": "pure SaaS, no warehouse operations"}
    )
    mocker.patch(
        "src.handlers.icp_gate.httpx.post", return_value=_resp(payload)
    )

    from src.handlers.icp_gate import check_icp_fit
    result = check_icp_fit("Notion")
    assert result == {"reason": "pure SaaS, no warehouse operations"}


def test_icp_gate_returns_none_when_in_icp(mocker, monkeypatch):
    from src.config import settings
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-test")

    mocker.patch(
        "src.handlers.icp_gate.httpx.post",
        return_value=_resp(json.dumps({"out_of_icp": False})),
    )

    from src.handlers.icp_gate import check_icp_fit
    assert check_icp_fit("Walmart") is None


def test_icp_gate_returns_none_on_parse_failure(mocker, monkeypatch):
    from src.config import settings
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-test")

    mocker.patch(
        "src.handlers.icp_gate.httpx.post",
        return_value=_resp("not json at all"),
    )

    from src.handlers.icp_gate import check_icp_fit
    assert check_icp_fit("Sysco") is None


def test_icp_gate_swallows_transport_failure(mocker, monkeypatch, caplog):
    from src.config import settings
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-test")

    mocker.patch(
        "src.handlers.icp_gate.httpx.post", side_effect=RuntimeError("boom")
    )

    from src.handlers.icp_gate import check_icp_fit
    with caplog.at_level("WARNING"):
        assert check_icp_fit("Acme") is None

    text = "\n".join(r.message for r in caplog.records)
    assert "RuntimeError" in text
    assert "boom" not in text  # str(e) must not leak (CLAUDE.md log hygiene)


def test_icp_gate_caches_repeat_calls(mocker, monkeypatch):
    from src.config import settings
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-test")

    mock_post = mocker.patch(
        "src.handlers.icp_gate.httpx.post",
        return_value=_resp(json.dumps({"out_of_icp": False})),
    )

    from src.handlers.icp_gate import check_icp_fit
    check_icp_fit("Walmart")
    check_icp_fit("Walmart")
    check_icp_fit("walmart")  # case-insensitive
    assert mock_post.call_count == 1


def test_icp_gate_missing_api_key_skips_call(mocker, monkeypatch):
    from src.config import settings
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "")

    mock_post = mocker.patch("src.handlers.icp_gate.httpx.post")

    from src.handlers.icp_gate import check_icp_fit
    assert check_icp_fit("anyone") is None
    mock_post.assert_not_called()


def test_icp_override_card_sanitizes_input():
    from src.handlers.icp_gate import icp_override_card

    blocks = icp_override_card(
        account_name="Evil<script>Co",
        reason="phishing|attempt>here",
        session_id="sess-X",
    )
    rendered = " ".join(
        b.get("text", {}).get("text", "")
        for b in blocks
        if isinstance(b.get("text"), dict)
    )
    # safe_mrkdwn strips angle brackets and pipes — confirm neither
    # appears in the rendered text.
    assert "<" not in rendered
    assert ">" not in rendered
    assert "|" not in rendered

    # Two action buttons: proceed + cancel.
    actions = [b for b in blocks if b.get("type") == "actions"]
    assert len(actions) == 1
    action_ids = {el["action_id"] for el in actions[0]["elements"]}
    assert action_ids == {"icp_override_proceed", "icp_override_cancel"}
