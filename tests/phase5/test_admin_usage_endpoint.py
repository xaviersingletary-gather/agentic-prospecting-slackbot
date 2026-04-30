"""Spec §1.5 — GET /admin/usage returns last 50 JSONL entries, descending."""
import json

import pytest
from aiohttp.test_utils import TestClient, TestServer


@pytest.fixture
def tmp_log(tmp_path):
    return tmp_path / "usage.jsonl"


@pytest.mark.asyncio
async def test_admin_usage_returns_entries_descending(tmp_log, monkeypatch):
    from src.usage.logger import log_usage
    from src.health import create_health_app

    log_usage({"timestamp": "2026-04-30T10:00:00Z", "account_queried": "A"}, log_path=tmp_log)
    log_usage({"timestamp": "2026-04-30T12:00:00Z", "account_queried": "B"}, log_path=tmp_log)
    log_usage({"timestamp": "2026-04-30T11:00:00Z", "account_queried": "C"}, log_path=tmp_log)

    monkeypatch.setenv("USAGE_LOG_PATH", str(tmp_log))
    monkeypatch.setenv("ADMIN_SLACK_USER_IDS", "U_ADMIN")

    async with TestClient(TestServer(create_health_app())) as client:
        resp = await client.get(
            "/admin/usage", headers={"X-Slack-User-ID": "U_ADMIN"}
        )
        assert resp.status == 200
        body = await resp.json()
        assert [e["account_queried"] for e in body["entries"]] == ["B", "C", "A"]


@pytest.mark.asyncio
async def test_admin_usage_caps_at_50(tmp_log, monkeypatch):
    from src.usage.logger import log_usage
    from src.health import create_health_app

    for i in range(75):
        log_usage(
            {"timestamp": f"2026-04-30T10:{i:02d}:00Z", "account_queried": f"A{i}"},
            log_path=tmp_log,
        )
    monkeypatch.setenv("USAGE_LOG_PATH", str(tmp_log))
    monkeypatch.setenv("ADMIN_SLACK_USER_IDS", "U_ADMIN")

    async with TestClient(TestServer(create_health_app())) as client:
        resp = await client.get(
            "/admin/usage", headers={"X-Slack-User-ID": "U_ADMIN"}
        )
        body = await resp.json()
        assert len(body["entries"]) == 50


@pytest.mark.asyncio
async def test_admin_usage_returns_empty_when_no_log(tmp_path, monkeypatch):
    from src.health import create_health_app

    monkeypatch.setenv("USAGE_LOG_PATH", str(tmp_path / "missing.jsonl"))
    monkeypatch.setenv("ADMIN_SLACK_USER_IDS", "U_ADMIN")

    async with TestClient(TestServer(create_health_app())) as client:
        resp = await client.get(
            "/admin/usage", headers={"X-Slack-User-ID": "U_ADMIN"}
        )
        assert resp.status == 200
        body = await resp.json()
        assert body["entries"] == []
