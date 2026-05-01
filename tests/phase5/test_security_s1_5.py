"""Security gates S1.5a (no raw slash-command text in logs) and S1.5b
(/admin/usage gated by ADMIN_SLACK_USER_IDS allowlist).
"""
import json

import pytest
from aiohttp.test_utils import TestClient, TestServer


SECRET_LEAK_TOKEN = "MY_SECRET_TOKEN_123_SHOULD_NEVER_LEAK"


@pytest.fixture
def tmp_log(tmp_path):
    return tmp_path / "usage.jsonl"


# ---------- S1.5a — raw user text never logged ----------

def test_redact_user_text_strips_secret_like_text(monkeypatch):
    from src.security.log_redact import redact_user_text

    out = redact_user_text(f"please research Kroger {SECRET_LEAK_TOKEN}")
    assert SECRET_LEAK_TOKEN not in out


def test_redact_user_text_returns_length_and_hash():
    from src.security.log_redact import redact_user_text

    out = redact_user_text("anything goes here")
    assert "len=" in out
    assert "sha256=" in out


def test_log_usage_does_not_persist_raw_query(tmp_log):
    from src.usage.logger import log_usage

    # If a caller mistakenly tries to log raw_query, the logger must not
    # write that field to disk.
    log_usage(
        {
            "slack_user_id": "U1",
            "account_queried": "Kroger",
            "raw_query": f"please research Kroger {SECRET_LEAK_TOKEN}",
        },
        log_path=tmp_log,
    )
    contents = tmp_log.read_text()
    assert SECRET_LEAK_TOKEN not in contents
    assert "raw_query" not in contents


# ---------- S1.5b — admin allowlist ----------

def test_is_admin_returns_true_when_user_in_env_list(monkeypatch):
    from src.security.admin_allowlist import is_admin

    monkeypatch.setenv("ADMIN_SLACK_USER_IDS", "U_ALPHA,U_BETA,U_GAMMA")
    assert is_admin("U_BETA") is True


def test_is_admin_returns_false_when_user_not_in_list(monkeypatch):
    from src.security.admin_allowlist import is_admin

    monkeypatch.setenv("ADMIN_SLACK_USER_IDS", "U_ALPHA")
    assert is_admin("U_NOT_LISTED") is False


def test_is_admin_returns_false_when_env_unset(monkeypatch):
    from src.security.admin_allowlist import is_admin

    monkeypatch.delenv("ADMIN_SLACK_USER_IDS", raising=False)
    assert is_admin("U_ALPHA") is False


def test_is_admin_handles_whitespace_in_env(monkeypatch):
    from src.security.admin_allowlist import is_admin

    monkeypatch.setenv("ADMIN_SLACK_USER_IDS", " U_ALPHA , U_BETA ")
    assert is_admin("U_ALPHA") is True
    assert is_admin("U_BETA") is True


@pytest.mark.asyncio
async def test_admin_usage_returns_403_for_non_admin(tmp_log, monkeypatch):
    from src.usage.logger import log_usage
    from src.health import create_health_app

    log_usage({"timestamp": "2026-04-30T10:00:00Z", "account_queried": "A"}, log_path=tmp_log)
    monkeypatch.setenv("USAGE_LOG_PATH", str(tmp_log))
    monkeypatch.setenv("ADMIN_SLACK_USER_IDS", "U_ADMIN")

    async with TestClient(TestServer(create_health_app())) as client:
        resp = await client.get(
            "/admin/usage", headers={"X-Slack-User-ID": "U_RANDOM"}
        )
        assert resp.status == 403
        body = await resp.json()
        # Body must not leak any usage data
        assert "entries" not in body or body.get("entries") in (None, [])


@pytest.mark.asyncio
async def test_admin_usage_returns_403_when_no_user_header(tmp_log, monkeypatch):
    from src.health import create_health_app

    monkeypatch.setenv("USAGE_LOG_PATH", str(tmp_log))
    monkeypatch.setenv("ADMIN_SLACK_USER_IDS", "U_ADMIN")

    async with TestClient(TestServer(create_health_app())) as client:
        resp = await client.get("/admin/usage")
        assert resp.status == 403


@pytest.mark.asyncio
async def test_admin_usage_returns_403_when_allowlist_empty(tmp_log, monkeypatch):
    from src.health import create_health_app

    monkeypatch.delenv("ADMIN_SLACK_USER_IDS", raising=False)
    monkeypatch.setenv("USAGE_LOG_PATH", str(tmp_log))

    async with TestClient(TestServer(create_health_app())) as client:
        resp = await client.get(
            "/admin/usage", headers={"X-Slack-User-ID": "U_ANY"}
        )
        assert resp.status == 403
