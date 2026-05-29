"""Phase 9 — shared synthesis helper.

The helper wraps a single OpenRouter call with the project's trust
posture: text-in / text-out, no tools, narrow timeout, type-name-only
logging, fallback on any failure mode.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.research.agents.synthesis import (
    FORBIDDEN_AI_TELLS,
    build_grounding_stanza,
    synthesize_with_fallback,
)


def _ok_response(content: str) -> MagicMock:
    r = MagicMock()
    r.status_code = 200
    r.raise_for_status.return_value = None
    r.json.return_value = {"choices": [{"message": {"content": content}}]}
    return r


def test_synthesize_returns_content_on_success(mocker, monkeypatch):
    from src.config import settings
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-test")

    mock_post = mocker.patch(
        "src.research.agents.synthesis.httpx.post",
        return_value=_ok_response("Synthesized prose."),
    )

    out = synthesize_with_fallback(
        system_prompt="You are a helper.",
        user_payload="claim payload",
        fallback="DETERMINISTIC",
    )
    assert out == "Synthesized prose."
    mock_post.assert_called_once()


def test_synthesize_returns_fallback_when_key_missing(monkeypatch, caplog):
    from src.config import settings
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "")

    with caplog.at_level("WARNING"):
        out = synthesize_with_fallback(
            system_prompt="x", user_payload="y", fallback="DETERMINISTIC",
        )
    assert out == "DETERMINISTIC"
    assert any("OPENROUTER_API_KEY" in r.message for r in caplog.records)


def test_synthesize_returns_fallback_on_http_error(mocker, monkeypatch, caplog):
    from src.config import settings
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-test")

    mocker.patch(
        "src.research.agents.synthesis.httpx.post",
        side_effect=RuntimeError("upstream 500"),
    )
    with caplog.at_level("ERROR"):
        out = synthesize_with_fallback(
            system_prompt="x", user_payload="y", fallback="DETERMINISTIC",
        )
    assert out == "DETERMINISTIC"
    log_text = "\n".join(r.message for r in caplog.records)
    assert "RuntimeError" in log_text
    # str(e) must NOT leak.
    assert "upstream 500" not in log_text


def test_synthesize_returns_fallback_on_empty_content(mocker, monkeypatch):
    from src.config import settings
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-test")

    mocker.patch(
        "src.research.agents.synthesis.httpx.post",
        return_value=_ok_response("   "),  # whitespace only
    )
    out = synthesize_with_fallback(
        system_prompt="x", user_payload="y", fallback="DETERMINISTIC",
    )
    assert out == "DETERMINISTIC"


def test_synthesize_passes_system_and_user_messages_correctly(mocker, monkeypatch):
    from src.config import settings
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-test")

    captured = {}

    def _capture(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        captured["timeout"] = timeout
        return _ok_response("ok")

    mocker.patch(
        "src.research.agents.synthesis.httpx.post", side_effect=_capture
    )

    synthesize_with_fallback(
        system_prompt="SYSTEM",
        user_payload="USER",
        fallback="FB",
        timeout_sec=3.5,
        max_tokens=400,
        temperature=0.1,
    )
    msgs = captured["json"]["messages"]
    assert msgs[0]["role"] == "system" and msgs[0]["content"] == "SYSTEM"
    assert msgs[1]["role"] == "user" and msgs[1]["content"] == "USER"
    assert captured["json"]["max_tokens"] == 400
    assert captured["json"]["temperature"] == 0.1
    assert captured["timeout"] == 3.5


def test_synthesize_empty_prompt_short_circuits(mocker, monkeypatch):
    from src.config import settings
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-test")
    mock_post = mocker.patch("src.research.agents.synthesis.httpx.post")

    out = synthesize_with_fallback(
        system_prompt="", user_payload="x", fallback="FB",
    )
    assert out == "FB"
    mock_post.assert_not_called()


def test_grounding_stanza_includes_forbidden_words_and_em_dash_rule():
    stanza = build_grounding_stanza(
        role_description="You are a sales research synthesizer."
    )
    assert "Ground every sentence" in stanza
    for word in FORBIDDEN_AI_TELLS:
        assert word in stanza
    assert "em-dashes" in stanza or "em-dash" in stanza
    assert "Do not call tools" in stanza


def test_forbidden_ai_tells_match_sdr_skill_list():
    """The SDR skill enumerates these exact words. Make sure the constant
    stays in sync — if a future edit removes one, this test catches it.
    """
    expected = {
        "leverage", "unlock", "robust", "seamless", "holistic", "utilize",
        "facilitate", "empower", "streamline", "cutting-edge",
        "transformative",
    }
    assert set(FORBIDDEN_AI_TELLS) == expected
