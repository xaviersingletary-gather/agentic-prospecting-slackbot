"""Phase 6 (V1 Daily-Use) — Move 3: suggested follow-up generator.

Given a completed research artifact + persona list, ask the LLM for 3-4
grounded next-question prompts that reference specific findings (named DC,
named contact, named trigger event). On any failure mode (missing key,
timeout, non-2xx, malformed JSON, list size out of [3, 4] range), return a
generic fallback list. The caller is responsible for logging a
``suggested_questions_failed`` ``WorkflowEvent`` — this module never writes
to the DB.

Security (CLAUDE.md):
- Never log ``OPENROUTER_API_KEY`` or the request body. Type-name only on
  any exception via ``safe_log_exception``.
- Pure text-in / text-out. No tool use. No fresh retrieval.
- Caller renders the questions through Slack Block Kit (plain_text button
  labels); ``safe_mrkdwn`` is not strictly required for plain_text but
  the runner-side wiring will still trim aggressive payloads via the
  Slack button text length cap.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Iterable, List, Optional

import httpx

from src.config import settings
from src.security.exception_logger import safe_log_exception

logger = logging.getLogger(__name__)

# Spec §5 Move 3 — 5s budget for this call. Past that the brief ships
# without buttons.
_TIMEOUT_SECONDS = 5
_MAX_TOKENS = 400
_TEMPERATURE = 0.4

# Spec §5 Move 3 — generic fallback if anything goes wrong.
FALLBACK_QUESTIONS: List[str] = [
    "Which contact should I hit first?",
    "What's the strongest trigger event to lead with?",
    "What are the biggest research gaps?",
]


_PROMPT_TEMPLATE = (
    "Given this account research, generate exactly 3-4 follow-up questions "
    "a sales rep would ask next to act on it. Each question must be "
    "answerable from the research above. Reference specific findings "
    "(a named DC, a named contact, a specific trigger event) — do not "
    "produce generic questions. Return ONLY a JSON array of strings. "
    "No prose, no markdown fences.\n\n"
    "## Account research\n{research_summary}\n\n"
    "## Personas on file\n{personas_summary}\n"
)


# ---------------------------------------------------------------------------
# Internal — summarize the inputs the LLM sees
# ---------------------------------------------------------------------------


def _render_research_summary(company_research: Any) -> str:
    """Render the saved CompanyResearch row into a compact prompt section.

    Accepts either a SQLAlchemy row or a dict-shaped fixture so the same
    helper covers production and tests.
    """
    if company_research is None:
        return "(no research artifact available)"

    def _get(key: str, default: Any = None) -> Any:
        if isinstance(company_research, dict):
            return company_research.get(key, default)
        return getattr(company_research, key, default)

    account_name = _get("account_name") or "(unknown account)"
    facility_count = _get("facility_count")
    facility_note = _get("facility_count_note") or ""
    board_initiatives = _get("board_initiatives") or []
    trigger_events = _get("trigger_events") or []
    automation_vendors = _get("automation_vendors") or []
    research_gaps = _get("research_gaps") or []
    raw_research_text = _get("raw_research_text") or ""

    if facility_count is None:
        facility_line = "Facility count: unknown"
    else:
        facility_line = f"Facility count: {facility_count}"
        if facility_note:
            facility_line += f" — {facility_note}"

    parts = [
        f"Account: {account_name}",
        facility_line,
        "Board initiatives:",
        _render_list(board_initiatives),
        "Trigger events:",
        _render_list(trigger_events),
        "Automation vendors:",
        _render_list(automation_vendors),
        "Research gaps:",
        _render_list(research_gaps),
    ]
    if raw_research_text:
        # Cap to keep the prompt small — Move 3 is a sidecar, not the main act.
        parts.append("Raw research excerpt:")
        parts.append(raw_research_text[:4_000])

    return "\n".join(parts)


def _render_personas_summary(personas: Optional[Iterable[Any]]) -> str:
    if not personas:
        return "(no personas approved yet)"
    lines: List[str] = []
    for p in personas:
        def _get(key: str) -> Any:
            if isinstance(p, dict):
                return p.get(key)
            return getattr(p, key, None)

        first = _get("first_name") or ""
        last = _get("last_name") or ""
        title = _get("title") or ""
        persona_type = _get("persona_type") or ""
        name = f"{first} {last}".strip() or "(name missing)"
        lines.append(f"- {name} — title={title}; persona_type={persona_type}")
    return "\n".join(lines)


def _render_list(items: Optional[Iterable[Any]]) -> str:
    if not items:
        return "  (none on file)"
    lines: List[str] = []
    for item in items:
        if isinstance(item, dict):
            label = (
                item.get("title")
                or item.get("description")
                or item.get("vendor_name")
                or item.get("summary")
                or ""
            )
            if label:
                lines.append(f"  - {label}")
            else:
                compact = ", ".join(f"{k}={v}" for k, v in item.items() if v)
                lines.append(f"  - {compact}")
        else:
            lines.append(f"  - {item}")
    return "\n".join(lines)


def _strip_fences(raw: str) -> str:
    """Pull a JSON array out of an LLM response, even with prose around it.

    Production reality: Haiku 4.5 frequently wraps the array in either
    ```json fences, a "Here are your questions:" preamble, or trailing
    explanatory text. Strict fence-stripping rejected all three. This
    version locates the first '[' and the last ']' and trusts json.loads
    to validate. Falls back to the raw string if no brackets found.
    """
    raw = (raw or "").strip()
    if not raw:
        return raw

    # Fast path: clean JSON array.
    if raw.startswith("[") and raw.endswith("]"):
        return raw

    # Strip ```json / ``` fences if present.
    if raw.startswith("```"):
        try:
            raw = raw.split("```", 2)[1]
            if raw.lower().startswith("json"):
                raw = raw[4:]
            raw = raw.rsplit("```", 1)[0]
            raw = raw.strip()
        except IndexError:
            pass

    # Locate the JSON array boundaries — handles prose preamble/postamble.
    start = raw.find("[")
    end = raw.rfind("]")
    if start != -1 and end != -1 and end > start:
        return raw[start : end + 1].strip()
    return raw.strip()


def _validate_questions(value: Any) -> Optional[List[str]]:
    """Return a list of 3-4 non-empty strings, or None."""
    if not isinstance(value, list):
        return None
    cleaned: List[str] = []
    for item in value:
        if not isinstance(item, str):
            return None
        stripped = item.strip()
        if not stripped:
            return None
        cleaned.append(stripped)
    if 3 <= len(cleaned) <= 4:
        return cleaned
    return None


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def generate_suggested_questions(
    company_research: Any,
    personas: Optional[Iterable[Any]],
) -> List[str]:
    """Ask the LLM for 3-4 next-question prompts grounded in the artifacts.

    On any failure: returns ``FALLBACK_QUESTIONS`` (3 generic items). The
    caller — currently ``src/research/runner.py`` — is responsible for
    logging a ``suggested_questions_failed`` ``WorkflowEvent`` when this
    helper returns the fallback list.
    """
    if not settings.OPENROUTER_API_KEY:
        logger.warning("[suggested_questions] OPENROUTER_API_KEY missing — returning fallback")
        return list(FALLBACK_QUESTIONS)

    prompt = _PROMPT_TEMPLATE.format(
        research_summary=_render_research_summary(company_research),
        personas_summary=_render_personas_summary(personas),
    )

    try:
        response = httpx.post(
            f"{settings.OPENROUTER_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.OPENROUTER_MODEL,
                "max_tokens": _MAX_TOKENS,
                "temperature": _TEMPERATURE,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        raw_text = response.json()["choices"][0]["message"]["content"]
    except Exception as e:  # noqa: BLE001 — type-only logging; never str(e)
        safe_log_exception(
            logger, e, "[suggested_questions] LLM call failed"
        )
        return list(FALLBACK_QUESTIONS)

    try:
        cleaned = _strip_fences(raw_text)
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as e:
        safe_log_exception(
            logger, e, "[suggested_questions] JSON parse failed"
        )
        return list(FALLBACK_QUESTIONS)
    except Exception as e:  # noqa: BLE001
        safe_log_exception(
            logger, e, "[suggested_questions] response handling failed"
        )
        return list(FALLBACK_QUESTIONS)

    validated = _validate_questions(parsed)
    if validated is None:
        logger.warning(
            "[suggested_questions] LLM returned invalid shape — returning fallback"
        )
        return list(FALLBACK_QUESTIONS)
    return validated


def is_fallback(questions: List[str]) -> bool:
    """True iff ``questions`` matches the generic fallback list verbatim.

    Used by the runner to decide whether to log a `_failed` event vs. ship
    the brief silently.
    """
    return questions == FALLBACK_QUESTIONS
