"""Phase 4 (V1 Daily-Use) — Move 2 agent: context builder + OpenRouter call.

Pure functions only. No DB I/O, no Slack I/O, no fresh retrieval. The Q&A
trust boundary is read-only summarization over already-persisted artifacts
(spec §5 Move 2; CLAUDE.md "LLM prompt injection blast radius").

Public surface:
    * ``build_followup_context`` — assemble the prompt string.
    * ``answer_followup``       — call OpenRouter, fallback on any failure.

Anything else in this module is private (leading underscore).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional

import httpx

from src.config import settings
from src.security.exception_logger import safe_log_exception

logger = logging.getLogger(__name__)

# Constants — mirror spec §5 Move 2 "Token budget".
_RAW_RESEARCH_CHAR_CAP = 12_000
_TOTAL_CONTEXT_CHAR_CAP = 28_000
_RAW_RESEARCH_TRUNCATE_STEP = 2_000
_TRUNCATION_MARKER = "\n[…truncated…]"

_FALLBACK_ANSWER = "_I couldn't think through that one — try again or rephrase?_"

_SYSTEM_STANZA = (
    "You are a sales research copilot answering a question for a Gather AI "
    "rep about a single target account. Ground every claim ONLY in the "
    "artifacts below — the account research summary, persona list, contact "
    "research, and prior thread messages. Do not invent facts, do not "
    "propose to fetch new sources, do not call tools. If the artifacts do "
    "not contain the answer, say so explicitly. Keep responses under 200 "
    "words unless the rep explicitly asks for more depth."
)


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------


def _truncate_raw_research(text: str, cap: int) -> str:
    if not text:
        return ""
    if len(text) <= cap:
        return text
    return text[:cap] + _TRUNCATION_MARKER


def _render_list(items: Optional[Iterable[Any]], empty_marker: str = "(none on file)") -> str:
    if not items:
        return f"  {empty_marker}"
    lines = []
    for item in items:
        if isinstance(item, dict):
            # Prefer a short summary field; fall back to a compact dict repr.
            label = (
                item.get("title")
                or item.get("description")
                or item.get("vendor_name")
                or item.get("summary")
                or ""
            )
            extras = []
            for key in ("source", "date", "category", "deployment_status", "relevance"):
                val = item.get(key)
                if val:
                    extras.append(f"{key}={val}")
            if label and extras:
                lines.append(f"  - {label} [{'; '.join(extras)}]")
            elif label:
                lines.append(f"  - {label}")
            else:
                # Last resort — render keys/values without dumping huge blobs.
                compact = ", ".join(f"{k}={v}" for k, v in item.items() if v)
                lines.append(f"  - {compact}")
        else:
            lines.append(f"  - {item}")
    return "\n".join(lines)


def _render_company_section(company_research: Any) -> str:
    if company_research is None:
        return (
            "## Account research summary\n"
            "  (research still in progress — no saved artifacts yet)"
        )

    facility_count = getattr(company_research, "facility_count", None)
    facility_note = getattr(company_research, "facility_count_note", None) or ""
    board_initiatives = getattr(company_research, "board_initiatives", None) or []
    trigger_events = getattr(company_research, "trigger_events", None) or []
    automation_vendors = getattr(company_research, "automation_vendors", None) or []
    research_gaps = getattr(company_research, "research_gaps", None) or []
    raw_research_text = getattr(company_research, "raw_research_text", None) or ""

    truncated_raw = _truncate_raw_research(raw_research_text, _RAW_RESEARCH_CHAR_CAP)

    if facility_count is None:
        facility_line = "  Facility count: unknown"
    else:
        facility_line = f"  Facility count: {facility_count}"
        if facility_note:
            facility_line += f" — {facility_note}"

    return (
        "## Account research summary\n"
        f"{facility_line}\n"
        "  Board initiatives:\n"
        f"{_render_list(board_initiatives)}\n"
        "  Trigger events:\n"
        f"{_render_list(trigger_events)}\n"
        "  Automation vendors:\n"
        f"{_render_list(automation_vendors)}\n"
        "  Research gaps:\n"
        f"{_render_list(research_gaps)}\n"
        "  Raw research text:\n"
        f"{truncated_raw}"
    )


def _render_contacts_section(personas: List[Any], contact_researches: List[Any]) -> str:
    if not personas:
        return "## Contacts\n  (no personas approved yet)"

    cr_by_persona: Dict[str, Any] = {}
    for cr in contact_researches or []:
        pid = getattr(cr, "persona_id", None)
        if pid:
            cr_by_persona[pid] = cr

    lines: List[str] = ["## Contacts"]
    for p in personas:
        first = getattr(p, "first_name", "") or ""
        last = getattr(p, "last_name", "") or ""
        title = getattr(p, "title", "") or ""
        persona_type = getattr(p, "persona_type", "") or ""
        seniority = getattr(p, "seniority", "") or ""
        priority = getattr(p, "priority_score", "") or ""
        value_driver = getattr(p, "value_driver", None)

        name = f"{first} {last}".strip() or "(name missing)"
        header = (
            f"  - {name} — title={title}; persona_type={persona_type}; "
            f"seniority={seniority}; priority_score={priority}"
        )
        if value_driver:
            header += f"; value_driver={value_driver}"
        lines.append(header)

        # Flagged = has a matching ContactResearch row.
        cr = cr_by_persona.get(getattr(p, "id", None))
        if cr is not None:
            tenure = getattr(cr, "current_role_tenure", None) or ""
            prior_roles = getattr(cr, "prior_roles", None) or []
            recent_linkedin = getattr(cr, "recent_linkedin", None) or []
            speaking_activity = getattr(cr, "speaking_activity", None) or ""

            lines.append(f"      current_role_tenure: {tenure}")
            lines.append("      prior_roles:")
            lines.append(_render_list(prior_roles, empty_marker="(none on file)").replace("  -", "        -"))
            lines.append("      recent_linkedin:")
            lines.append(_render_list(recent_linkedin, empty_marker="(none on file)").replace("  -", "        -"))
            lines.append(f"      speaking_activity: {speaking_activity or '(none on file)'}")

    return "\n".join(lines)


def _render_thread_history(thread_history: List[Dict[str, Any]]) -> str:
    if not thread_history:
        return "## Thread so far\n  (none — this is the first follow-up)"

    lines = ["## Thread so far (oldest first)"]
    for msg in thread_history:
        user = msg.get("user", "?") if isinstance(msg, dict) else "?"
        text = msg.get("text", "") if isinstance(msg, dict) else ""
        lines.append(f"  [{user}] {text}")
    return "\n".join(lines)


def _assemble(
    account_name: str,
    company_section: str,
    contacts_section: str,
    history_section: str,
    question: str,
) -> str:
    return (
        f"{_SYSTEM_STANZA}\n\n"
        f"# Account: {account_name}\n\n"
        f"{company_section}\n\n"
        f"{contacts_section}\n\n"
        f"{history_section}\n\n"
        f"Current question:\n{question}\n"
    )


def build_followup_context(
    session: Any,
    company_research: Optional[Any],
    personas: List[Any],
    contact_researches: List[Any],
    thread_history: List[Dict[str, Any]],
    question: str,
) -> str:
    """Assemble the full Q&A prompt as a single string.

    Pure: no DB, no HTTP, no Slack. Caller is responsible for fetching
    rows. Output is fed directly to ``answer_followup``.
    """
    account_name = getattr(session, "account_name", None) or "(unknown account)"

    company_section = _render_company_section(company_research)
    contacts_section = _render_contacts_section(personas or [], contact_researches or [])
    history_section = _render_thread_history(thread_history or [])

    context = _assemble(
        account_name,
        company_section,
        contacts_section,
        history_section,
        question,
    )

    # Token-budget guard: if the assembled context overruns the cap, walk
    # raw_research_text down in 2k-char steps until we fit. All other
    # sections are preserved verbatim.
    if len(context) <= _TOTAL_CONTEXT_CHAR_CAP or company_research is None:
        return context

    raw_research_text = getattr(company_research, "raw_research_text", None) or ""
    current_cap = _RAW_RESEARCH_CHAR_CAP
    while len(context) > _TOTAL_CONTEXT_CHAR_CAP and current_cap > 0:
        current_cap = max(0, current_cap - _RAW_RESEARCH_TRUNCATE_STEP)
        truncated_raw = _truncate_raw_research(raw_research_text, current_cap)
        # Rebuild only the company section against the same fields.
        facility_count = getattr(company_research, "facility_count", None)
        facility_note = getattr(company_research, "facility_count_note", None) or ""
        board_initiatives = getattr(company_research, "board_initiatives", None) or []
        trigger_events = getattr(company_research, "trigger_events", None) or []
        automation_vendors = getattr(company_research, "automation_vendors", None) or []
        research_gaps = getattr(company_research, "research_gaps", None) or []

        if facility_count is None:
            facility_line = "  Facility count: unknown"
        else:
            facility_line = f"  Facility count: {facility_count}"
            if facility_note:
                facility_line += f" — {facility_note}"

        company_section = (
            "## Account research summary\n"
            f"{facility_line}\n"
            "  Board initiatives:\n"
            f"{_render_list(board_initiatives)}\n"
            "  Trigger events:\n"
            f"{_render_list(trigger_events)}\n"
            "  Automation vendors:\n"
            f"{_render_list(automation_vendors)}\n"
            "  Research gaps:\n"
            f"{_render_list(research_gaps)}\n"
            "  Raw research text:\n"
            f"{truncated_raw}"
        )
        context = _assemble(
            account_name,
            company_section,
            contacts_section,
            history_section,
            question,
        )

    return context


# ---------------------------------------------------------------------------
# OpenRouter call
# ---------------------------------------------------------------------------


def answer_followup(context: str) -> str:
    """Call OpenRouter with the assembled context; return the answer text.

    On any failure (missing key, timeout, non-2xx, malformed JSON, etc.),
    log the exception TYPE only and return ``_FALLBACK_ANSWER``.
    Never logs API keys, never logs raw user text, never logs ``str(e)``.
    """
    if not settings.OPENROUTER_API_KEY:
        logger.warning("[followup_agent] OPENROUTER_API_KEY not set — returning fallback")
        return _FALLBACK_ANSWER

    try:
        response = httpx.post(
            f"{settings.OPENROUTER_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.OPENROUTER_MODEL,
                "max_tokens": 800,
                "temperature": 0.3,
                "messages": [{"role": "user", "content": context}],
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:  # noqa: BLE001 — type-name logged only, no str(e)
        safe_log_exception(logger, e, "[followup_agent] OpenRouter call failed")
        return _FALLBACK_ANSWER
