"""`/research-bot-stats` admin slash command (May 26 V1 spec, Phase 6).

Reads recent JSONL entries via `read_recent` and formats a summary
report for Slack. Admin-allowlisted via `assert_admin`; non-admins
get a polite 403-style reply.

Summary shape:
  - Last 50 entries scanned (configurable via `LIMIT_DEFAULT`).
  - Per-event counts: new_query / followup / agent_failure / disambig.
  - For new_query: avg total_ms, top errored_agents.
  - For followup: avg latency_ms, v2-path adoption %.
  - For agent_failure: count grouped by agent_name.

No raw question text is ever surfaced — only the lengths/IDs the
JSONL persisted. Slack output passes through safe_mrkdwn.
"""
from __future__ import annotations

import logging
from collections import Counter
from typing import Any, Callable, Dict, List, Optional

from src.security.admin_allowlist import is_admin
from src.security.safe_mrkdwn import safe_mrkdwn
from src.usage.logger import read_recent

logger = logging.getLogger(__name__)

LIMIT_DEFAULT = 100


def build_stats_blocks(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Pure: entries → Block Kit. Tests assert on this directly."""
    counts: Counter = Counter()
    new_query_durations: List[int] = []
    followup_latencies: List[int] = []
    followup_v2_count = 0
    followup_total = 0
    errored_agent_counts: Counter = Counter()
    agent_failure_counts: Counter = Counter()

    for entry in entries:
        et = entry.get("event_type") or "unknown"
        counts[et] += 1
        if et == "new_query":
            ms = entry.get("total_ms")
            if isinstance(ms, int):
                new_query_durations.append(ms)
            for a in entry.get("errored_agents") or []:
                errored_agent_counts[a] += 1
        elif et == "followup":
            ms = entry.get("latency_ms")
            if isinstance(ms, int):
                followup_latencies.append(ms)
            followup_total += 1
            if entry.get("used_v2_path"):
                followup_v2_count += 1
        elif et == "agent_failure":
            agent_failure_counts[entry.get("agent_name") or "?"] += 1

    def _avg(xs: List[int]) -> str:
        return f"{sum(xs) / len(xs):.0f}ms" if xs else "n/a"

    v2_pct = (
        f"{(followup_v2_count / followup_total) * 100:.0f}%"
        if followup_total
        else "n/a"
    )

    lines = [
        f"*Last {len(entries)} events*",
        f"• `new_query`: {counts.get('new_query', 0)}  "
        f"(avg {_avg(new_query_durations)})",
        f"• `followup`: {counts.get('followup', 0)}  "
        f"(avg {_avg(followup_latencies)}, v2-path {v2_pct})",
        f"• `agent_failure`: {counts.get('agent_failure', 0)}",
        f"• `disambig`: {counts.get('disambig', 0)}",
    ]

    if errored_agent_counts:
        top = ", ".join(
            f"{safe_mrkdwn(name)}={n}"
            for name, n in errored_agent_counts.most_common(5)
        )
        lines.append(f"\n*Agents errored inside runs (top 5):* {top}")

    if agent_failure_counts:
        top = ", ".join(
            f"{safe_mrkdwn(name)}={n}"
            for name, n in agent_failure_counts.most_common(5)
        )
        lines.append(f"*Agent failures (standalone events):* {top}")

    return [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "📊 Research bot stats",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "\n".join(lines)},
        },
    ]


def handle_stats_command(
    payload: Dict[str, Any],
    ack: Callable[..., Any],
    respond: Callable[..., Any],
    *,
    limit: int = LIMIT_DEFAULT,
    log_path: Optional[str] = None,
) -> None:
    """Bolt `@app.command("/research-bot-stats")` entry point.

    `payload` is the Slack slash-command payload (has `user_id`).
    Admin-gated: non-admins get an ephemeral refusal that does not
    confirm whether the command exists for them.
    """
    ack()
    user_id = (payload or {}).get("user_id") or ""

    if not is_admin(user_id):
        # Same response shape as other admin-gated endpoints — don't
        # leak feature presence to non-admins.
        respond(response_type="ephemeral", text="Command not available.")
        return

    try:
        entries = read_recent(limit=limit, log_path=log_path)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[stats] read_recent failed: %s", type(e).__name__
        )
        respond(
            response_type="ephemeral",
            text="Could not read the usage log.",
        )
        return

    respond(
        response_type="ephemeral",
        blocks=build_stats_blocks(entries),
        text=f"{len(entries)} recent events.",
    )
