"""Session authorization primitive (spec gate S1.3, CLAUDE.md → Authorization).

Workspace membership is not authorization — anyone can click a button on
someone else's thread. Every state-mutating Slack handler must call
`assert_session_owner` before mutating.

Reused by every state-mutating handler from Phase 3 onward.
"""


class UnauthorizedSessionError(PermissionError):
    pass


def assert_session_owner(session_rep_id: str, action_user_id: str) -> None:
    if not session_rep_id or session_rep_id != action_user_id:
        raise UnauthorizedSessionError(
            "session does not belong to the user who triggered the action"
        )
