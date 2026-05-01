"""Admin allowlist (spec gate S1.5b, CLAUDE.md → Authorization).

`/admin/usage` and any future `/admin/*` endpoint reads `ADMIN_SLACK_USER_IDS`
(comma-separated Slack user IDs) and rejects callers not in the list. Empty
or unset env var = no admins (the safe default).
"""
import os
from typing import Set


def _admin_set() -> Set[str]:
    raw = os.getenv("ADMIN_SLACK_USER_IDS", "")
    return {chunk.strip() for chunk in raw.split(",") if chunk.strip()}


def is_admin(user_id: str) -> bool:
    if not user_id:
        return False
    return user_id in _admin_set()


class AdminRequiredError(PermissionError):
    pass


def assert_admin(user_id: str) -> None:
    if not is_admin(user_id):
        raise AdminRequiredError("admin allowlist rejected caller")
