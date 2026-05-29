"""Shared LLM-synthesis helper for the May 29 V1.1 agents.

Wraps a single OpenRouter call with the project's trust posture:
- text-in / text-out, no tools wired
- narrow timeout (6s default) so a slow LLM never blocks a research run
- type-name-only logging via `safe_log_exception`
- always returns a string — on any failure mode (missing key, timeout,
  non-2xx, malformed JSON, empty content) it returns the caller's
  fallback so the agent can still emit its deterministic scaffolding

Agents 7, 9, and 10 use this to turn keyword-matched / table-lookup
scaffolding into prose openers / per-persona angles / why-now lines.
Each agent constructs its own system prompt; this helper is plumbing.
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

from src.config import settings
from src.security.exception_logger import safe_log_exception

logger = logging.getLogger(__name__)

# Default cap on output length. Synthesis output is short by design —
# 2-3 sentences per persona / per hook. Larger values invite the model
# to ramble and burn tokens.
DEFAULT_MAX_TOKENS = 700
DEFAULT_TIMEOUT_SEC = 6.0
DEFAULT_TEMPERATURE = 0.2

# Vocabulary the SDR skill explicitly forbids. Each agent's system prompt
# should embed this list verbatim so the LLM has no excuse. We do NOT
# post-process the output to scrub these words — that's a V1.1 hardening
# step. For V1 we rely on the system prompt + sample-output spot checks.
FORBIDDEN_AI_TELLS = [
    "leverage", "unlock", "robust", "seamless", "holistic", "utilize",
    "facilitate", "empower", "streamline", "cutting-edge", "transformative",
]

# Same source: no em-dashes in the rendered output (the SDR brief format
# explicitly bans them). Each agent's system prompt should include this.
FORBIDDEN_PUNCTUATION_NOTE = (
    "Never use em-dashes (—). Use a period or comma instead."
)


def synthesize_with_fallback(
    *,
    system_prompt: str,
    user_payload: str,
    fallback: str,
    timeout_sec: float = DEFAULT_TIMEOUT_SEC,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float = DEFAULT_TEMPERATURE,
    log_label: str = "[synthesis]",
) -> str:
    """Run one synthesis call against OpenRouter, return prose or fallback.

    The caller is responsible for:
      - drafting a grounded system_prompt that forbids fabrication
      - assembling the user_payload from already-validated Claims only
      - providing a deterministic fallback string for failure modes

    This function NEVER raises. On any failure mode it logs the
    exception type and returns the fallback.
    """
    if not settings.OPENROUTER_API_KEY:
        logger.warning("%s OPENROUTER_API_KEY not set — returning fallback", log_label)
        return fallback

    if not system_prompt or not user_payload:
        logger.warning("%s empty prompt or payload — returning fallback", log_label)
        return fallback

    try:
        response = httpx.post(
            f"{settings.OPENROUTER_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.OPENROUTER_MODEL,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_payload},
                ],
            },
            timeout=timeout_sec,
        )
        response.raise_for_status()
        data = response.json()
        content = (
            (data.get("choices") or [{}])[0]
            .get("message", {})
            .get("content")
            or ""
        ).strip()
        if not content:
            logger.warning("%s empty content from LLM — returning fallback", log_label)
            return fallback
        return content
    except Exception as e:  # noqa: BLE001 — type-name only, no str(e)
        safe_log_exception(logger, e, f"{log_label} OpenRouter call failed")
        return fallback


def build_grounding_stanza(*, role_description: str) -> str:
    """Common grounding rules every synthesis agent embeds in its prompt.

    Returns a multi-line stanza covering:
      - the role / output expectation
      - "ground every sentence in the artifacts below"
      - "do not invent facts; do not propose to fetch new sources"
      - "do not call tools (none are available)"
      - SDR-skill anti-AI-tell vocabulary list
      - "never use em-dashes"

    Agents append their own task-specific stanza after this block.
    """
    forbidden_words = ", ".join(FORBIDDEN_AI_TELLS)
    return (
        f"{role_description}\n\n"
        "Ground every sentence in the artifacts the user message provides. "
        "Do not invent facts. Do not propose to fetch new sources. "
        "Do not call tools — none are available.\n\n"
        "Vocabulary rules:\n"
        f"- Never use these AI-tell words: {forbidden_words}.\n"
        f"- {FORBIDDEN_PUNCTUATION_NOTE}\n"
        "- Write like an experienced B2B AE, not a marketing page.\n"
    )
