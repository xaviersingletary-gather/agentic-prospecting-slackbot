"""Research runner — the seam from persona-select to a formatted research
dump posted back to Slack.

Phase 11 wired the real Exa + Claude research pipeline behind
`build_findings`. The runner is now a thin shim. `build_placeholder_findings`
is preserved as a deprecated alias for back-compat with Phase 9 tests
and any caller that imported the old name.
"""
from typing import Any, Callable, Dict

from src.research.findings_builder import build_findings
from src.research.output_formatter import build_research_blocks
from src.research.sessions import ResearchSession


def build_placeholder_findings(session: ResearchSession) -> Dict[str, Any]:
    """Deprecated alias. Delegates to `build_findings`. Kept so existing
    callers (Phase 9 tests, handlers that imported the old name) keep
    working without modification."""
    return build_findings(session)


def run_research(session: ResearchSession, respond: Callable[..., Any]) -> None:
    findings = build_findings(session)
    blocks = build_research_blocks(findings)
    respond(
        response_type="ephemeral",
        replace_original=True,
        blocks=blocks,
        text=f"Research for {session.account_name}",
    )
