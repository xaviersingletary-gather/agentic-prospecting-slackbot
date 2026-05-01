"""Spec §1.5 — usage tracking JSONL.

One JSON object per line in `./logs/usage.jsonl`. The logger refuses to
persist the `raw_query` field on principle (S1.5a); callers must redact
slash-command text before passing anything user-typed downstream.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

# Fields that must never be persisted regardless of caller intent.
_FORBIDDEN_FIELDS = frozenset({"raw_query", "raw_text", "command_text", "user_input"})

DEFAULT_LOG_PATH = "./logs/usage.jsonl"


def _resolve_path(log_path: Optional[Union[str, os.PathLike]]) -> Path:
    if log_path is None:
        log_path = os.getenv("USAGE_LOG_PATH", DEFAULT_LOG_PATH)
    return Path(log_path)


def _scrub(entry: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in entry.items() if k not in _FORBIDDEN_FIELDS}


def log_usage(
    entry: Dict[str, Any],
    log_path: Optional[Union[str, os.PathLike]] = None,
) -> None:
    path = _resolve_path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    safe_entry = _scrub(entry)
    safe_entry.setdefault(
        "timestamp",
        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )

    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(safe_entry) + "\n")


def read_recent(
    limit: int = 50,
    log_path: Optional[Union[str, os.PathLike]] = None,
) -> List[Dict[str, Any]]:
    path = _resolve_path(log_path)
    if not path.exists():
        return []

    entries: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    entries.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
    return entries[:limit]
