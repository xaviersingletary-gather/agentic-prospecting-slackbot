"""Minimal research runner — the seam from persona-select to a formatted
research dump posted back to Slack.

Today this builds a placeholder findings dict so the loop closes
end-to-end. A later phase will replace `build_placeholder_findings` with
the real Exa + Apollo + HubSpot pipeline.
"""
from typing import Any, Callable, Dict

from src.research.output_formatter import build_research_blocks
from src.research.sessions import ResearchSession


def build_placeholder_findings(session: ResearchSession) -> Dict[str, Any]:
    selected = ", ".join(session.personas) if session.personas else "none"
    return {
        "account_name": session.account_name,
        "trigger_events": [],
        "competitor_signals": [],
        "dc_intel": [],
        "board_initiatives": [],
        "research_gaps": [
            f"Selected personas: {selected}.",
            "Live Exa + Apollo + HubSpot research pipeline pending — "
            "this is a placeholder rendered through the v1 output format.",
        ],
    }


def run_research(session: ResearchSession, respond: Callable[..., Any]) -> None:
    findings = build_placeholder_findings(session)
    blocks = build_research_blocks(findings)
    respond(
        response_type="ephemeral",
        replace_original=True,
        blocks=blocks,
        text=f"Research for {session.account_name}",
    )
