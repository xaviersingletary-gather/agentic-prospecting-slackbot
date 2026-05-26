"""Typed JSONL event helpers for the May 26 V1 spec (Phase 6).

Wraps `log_usage()` so every event lands in the JSONL with a consistent
`event_type` discriminator and the spec-required fields per type.

Event types:
  - new_query     (one per research run)
  - followup      (one per follow-up question answered)
  - agent_failure (one per per-agent failure inside a run)
  - disambig      (one per disambiguation button click)

Hygiene per CLAUDE.md:
  - Never persist raw question text. Question/answer lengths only.
  - Never persist exception strings. Exception type names only.
  - Account names ARE persisted — they're shared sales-team context.

`/research-bot-stats` reads back from the same JSONL via `read_recent`.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.usage.logger import log_usage

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Event emitters — never raise (logging must not break the request path).
# ---------------------------------------------------------------------------


def _safe_emit(entry: Dict[str, Any]) -> None:
    try:
        log_usage(entry)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[v1_events] usage log write failed: %s", type(e).__name__
        )


def log_new_query(
    *,
    account_name: str,
    rep_id: str,
    intent: Optional[str],
    thread_ts: Optional[str],
    total_ms: int,
    agent_durations_ms: Dict[str, int],
    errored_agents: List[str],
    total_claims: int,
) -> None:
    """Emitted once `run_v1_research` completes (success or partial)."""
    _safe_emit(
        {
            "event_type": "new_query",
            "account_name": account_name,
            "rep_id": rep_id,
            "intent": intent,
            "thread_ts": thread_ts,
            "total_ms": total_ms,
            "agent_durations_ms": agent_durations_ms,
            "errored_agents": errored_agents,
            "total_claims": total_claims,
        }
    )


def log_followup(
    *,
    thread_ts: str,
    rep_id: str,
    question_length: int,
    answer_length: int,
    latency_ms: int,
    used_v2_path: bool,
) -> None:
    """Emitted after a follow-up Q&A response posts."""
    _safe_emit(
        {
            "event_type": "followup",
            "thread_ts": thread_ts,
            "rep_id": rep_id,
            "question_length": question_length,
            "answer_length": answer_length,
            "latency_ms": latency_ms,
            "used_v2_path": used_v2_path,
        }
    )


def log_agent_failure(
    *,
    agent_name: str,
    account_name: str,
    error_type: str,
    recovery: str,
) -> None:
    """Emitted when an individual agent in the dispatcher fails."""
    _safe_emit(
        {
            "event_type": "agent_failure",
            "agent_name": agent_name,
            "account_name": account_name,
            "error_type": error_type,
            "recovery": recovery,
        }
    )


def log_disambig(
    *,
    account_name: str,
    rep_id: str,
    candidates: List[str],
    chosen: Optional[str],
) -> None:
    """Emitted when the disambig card is posted (chosen=None) or a
    candidate is clicked (chosen=<label>).
    """
    _safe_emit(
        {
            "event_type": "disambig",
            "account_name": account_name,
            "rep_id": rep_id,
            "candidates": candidates,
            "chosen": chosen,
        }
    )
