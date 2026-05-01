"""Block Kit message: account name + 4 persona checkboxes + Run Research button.

The session id is embedded in both the actions block_id and the button
value so the action handler can look up the session deterministically.
"""
from typing import Any, Dict, List

from src.research.personas import PERSONAS
from src.security.safe_mrkdwn import safe_mrkdwn


def build_persona_select_blocks(account_name: str, session_id: str) -> List[Dict[str, Any]]:
    safe_account = safe_mrkdwn(account_name)
    options = [
        {
            "text": {"type": "plain_text", "text": meta["label"], "emoji": True},
            "value": key,
        }
        for key, meta in PERSONAS.items()
    ]
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"Research target: *{safe_account}*\n"
                    "Select at least one persona to scope the contact pull."
                ),
            },
        },
        {
            "type": "actions",
            "block_id": f"persona_select::{session_id}",
            "elements": [
                {
                    "type": "checkboxes",
                    "action_id": "persona_checkboxes",
                    "options": options,
                },
                {
                    "type": "button",
                    "action_id": "run_research",
                    "text": {"type": "plain_text", "text": "Run Research"},
                    "style": "primary",
                    "value": session_id,
                },
            ],
        },
    ]
