"""Per-account JSONL snapshot store for the V1.5 memory layer.

Layout: ``logs/account_snapshots/{account_key}.jsonl`` — append-only.
Each line is a single JSON object:

    {
        "account_name": "PepsiCo",
        "account_key":  "pepsico",
        "saved_at":     "2026-05-06T18:42:11Z",
        "findings":     { ...the structured research output... }
    }

`get_latest_snapshot` returns the most recent entry (last non-empty line)
or ``None`` for cold-start accounts. `save_snapshot` is fire-and-forget
from the caller's perspective: it logs and swallows OSError so a disk
failure cannot break the research response.

Account key normalization is intentionally simple: lowercase, strip
common corporate suffixes (Inc / LLC / Corp / Ltd / Co), collapse
non-alphanumerics to underscores. This is the same canonicalization
problem flagged in the V1.5 spec (open question #3); when V1 adopts a
stricter ID scheme (HubSpot company id, etc.), swap it in here.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

SNAPSHOT_DIR_ENV = "ACCOUNT_SNAPSHOT_DIR"
DEFAULT_SNAPSHOT_DIR = "logs/account_snapshots"

_SUFFIX_PATTERN = re.compile(
    r"(?:[\s,]+(?:inc|incorporated|llc|l\.l\.c\.?|corp|corporation|"
    r"ltd|limited|co|company|plc|gmbh|sa|ag)\.?)+$",
    re.IGNORECASE,
)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_MAX_KEY_LEN = 80


def _snapshot_dir() -> str:
    return os.environ.get(SNAPSHOT_DIR_ENV) or DEFAULT_SNAPSHOT_DIR


def normalize_account_key(account_name: str) -> str:
    """Filesystem-safe slug for an account name.

    Lowercases, strips common corporate suffixes, collapses non-alphanumerics
    to underscores. Returns an empty string only for empty / all-punctuation
    input — callers should treat that as "do not persist."
    """
    if not account_name:
        return ""
    s = account_name.strip().lower()
    s = _SUFFIX_PATTERN.sub(" ", s)
    s = _NON_ALNUM.sub("_", s).strip("_")
    if len(s) > _MAX_KEY_LEN:
        s = s[:_MAX_KEY_LEN].rstrip("_")
    return s


def _path_for(account_key: str) -> str:
    return os.path.join(_snapshot_dir(), f"{account_key}.jsonl")


def save_snapshot(account_name: str, findings: Dict[str, Any]) -> Optional[str]:
    """Append a snapshot for ``account_name``. Returns the file path on
    success or ``None`` on any failure (logged, never raised)."""
    key = normalize_account_key(account_name)
    if not key:
        logger.warning("[memory.snapshots] empty account key; skipping save")
        return None
    if not isinstance(findings, dict):
        logger.warning("[memory.snapshots] findings is not a dict; skipping save")
        return None

    record = {
        "account_name": account_name,
        "account_key": key,
        "saved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "findings": findings,
    }
    path = _path_for(key)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return path
    except OSError as e:
        logger.warning(
            "[memory.snapshots] save failed key=%s err=%s",
            key, type(e).__name__,
        )
        return None


def get_latest_snapshot(account_name: str) -> Optional[Dict[str, Any]]:
    """Return the most recent snapshot for ``account_name`` or ``None``.

    Tolerates partial / corrupt lines: scans from end-of-file backward
    and returns the first parseable JSON object. A completely unparseable
    file yields ``None``.
    """
    key = normalize_account_key(account_name)
    if not key:
        return None
    path = _path_for(key)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError as e:
        logger.warning(
            "[memory.snapshots] read failed key=%s err=%s",
            key, type(e).__name__,
        )
        return None
    for raw in reversed(lines):
        raw = raw.strip()
        if not raw:
            continue
        try:
            record = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(record, dict) and isinstance(record.get("findings"), dict):
            return record
    return None
