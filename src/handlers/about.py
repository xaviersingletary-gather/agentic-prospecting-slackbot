"""Spec §1.6 — `/about` slash command.

Returns an ephemeral Block Kit message with current version, capabilities,
upcoming roadmap items (V1.2 / V1.3 / V2.0) and a contact line.
"""
from typing import Any, Awaitable, Callable, Dict, List

from src.config import VERSION


def build_about_blocks() -> List[Dict[str, Any]]:
    return [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"📒 Account Research Bot — v{VERSION}",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "*What it does today*\n"
                    "• `/research [account]` — pulls trigger events, "
                    "competitor signals, DC intel, board initiatives, "
                    "research gaps, every claim sourced.\n"
                    "• Persona scoping — choose any of the four ICP "
                    "personas (CSCO, VP Warehouse Ops, VP Inventory & "
                    "Planning, S&OP Lead) before research runs.\n"
                    "• Citations enforced — unsourced claims flagged "
                    "`⚠️ [Unverified]`; unsourced DC counts blocked."
                ),
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "*Coming next*\n"
                    "• *V1.2* — HubSpot existence checks + account "
                    "snapshot, ICP score surfacing.\n"
                    "• *V1.3* — Salesforce account enrichment + Lucid "
                    "Charts influence-map cross-reference.\n"
                    "• *V2.0* — context-grounded outreach briefs and "
                    "Apollo sequence push (with explicit AE confirm)."
                ),
            },
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "Questions? Ping Xavier in #gtm-engineering.",
                }
            ],
        },
    ]


async def _maybe_await(value):
    if hasattr(value, "__await__"):
        return await value
    return value


async def handle_about_command(
    payload: Dict[str, Any],
    ack: Callable[..., Awaitable[Any]],
    respond: Callable[..., Awaitable[Any]],
) -> None:
    await _maybe_await(ack())
    await respond(
        response_type="ephemeral",
        blocks=build_about_blocks(),
        text=f"Account Research Bot v{VERSION}",
    )
