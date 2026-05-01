"""Spec §1.5 — Slack user_id → display_name resolver with per-session cache.

If `users.info` fails (network error, user_not_found, scope missing), the
resolver returns the raw user_id and `name_resolution_failed=True` so the
log entry can record the failure rather than crash the request.
"""
from typing import Any, Dict, Tuple


def resolve_slack_user_name(
    client: Any,
    user_id: str,
    cache: Dict[str, Tuple[str, bool]],
) -> Tuple[str, bool]:
    if user_id in cache:
        return cache[user_id]

    try:
        resp = client.users_info(user=user_id)
    except Exception:
        result = (user_id, True)
        cache[user_id] = result
        return result

    if not isinstance(resp, dict) or not resp.get("ok"):
        result = (user_id, True)
        cache[user_id] = result
        return result

    user = resp.get("user") or {}
    profile = user.get("profile") or {}
    name = (
        profile.get("display_name")
        or user.get("real_name")
        or user.get("name")
        or user_id
    )
    result = (name, False)
    cache[user_id] = result
    return result
