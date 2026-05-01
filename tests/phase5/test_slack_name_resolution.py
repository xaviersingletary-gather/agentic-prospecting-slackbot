"""Spec §1.5 — resolve Slack user_id → display name; cache for the session;
fail soft and flag `name_resolution_failed: true` if the API errors.
"""
from unittest.mock import MagicMock


def test_resolves_display_name_from_users_info():
    from src.usage.slack_user import resolve_slack_user_name

    client = MagicMock()
    client.users_info.return_value = {
        "ok": True,
        "user": {"id": "U1", "profile": {"display_name": "Xavier"},
                 "real_name": "Xavier Singletary", "name": "xavier"},
    }
    name, failed = resolve_slack_user_name(client, "U1", cache={})
    assert name == "Xavier"
    assert failed is False


def test_falls_back_to_real_name_when_display_name_empty():
    from src.usage.slack_user import resolve_slack_user_name

    client = MagicMock()
    client.users_info.return_value = {
        "ok": True,
        "user": {"id": "U1", "profile": {"display_name": ""},
                 "real_name": "Xavier Singletary", "name": "xavier"},
    }
    name, failed = resolve_slack_user_name(client, "U1", cache={})
    assert name == "Xavier Singletary"
    assert failed is False


def test_caches_resolution_per_session():
    from src.usage.slack_user import resolve_slack_user_name

    client = MagicMock()
    client.users_info.return_value = {
        "ok": True,
        "user": {"profile": {"display_name": "Xavier"}, "real_name": "X", "name": "x"},
    }
    cache: dict = {}
    resolve_slack_user_name(client, "U1", cache=cache)
    resolve_slack_user_name(client, "U1", cache=cache)
    resolve_slack_user_name(client, "U1", cache=cache)
    assert client.users_info.call_count == 1


def test_returns_failure_flag_when_api_raises():
    from src.usage.slack_user import resolve_slack_user_name

    client = MagicMock()
    client.users_info.side_effect = RuntimeError("network down")
    name, failed = resolve_slack_user_name(client, "U_LOST", cache={})
    assert failed is True
    # Returns the raw id when name unresolvable
    assert name == "U_LOST"


def test_returns_failure_flag_when_api_returns_not_ok():
    from src.usage.slack_user import resolve_slack_user_name

    client = MagicMock()
    client.users_info.return_value = {"ok": False, "error": "user_not_found"}
    name, failed = resolve_slack_user_name(client, "U_LOST", cache={})
    assert failed is True
    assert name == "U_LOST"
