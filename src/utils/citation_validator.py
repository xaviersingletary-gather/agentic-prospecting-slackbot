"""Spec §1.4 — citation post-processor.

Two responsibilities:

1. `flag_if_unverified(line)` — if a fact bullet looks like an assertion
   but lacks a Source URL, prefix it with `⚠️ [Unverified]`.

2. `is_unsourced_dc_count(line)` — DC count claims (number + 'distribution
   center' or 'DC') without a source must be **blocked entirely**, not
   flagged. The output_formatter calls this to drop matching items before
   rendering the DC intel section.

Heuristics here are intentionally loose. The agent's system prompt is the
primary defence; this is a safety net for any unsourced claim that slips
through.
"""
import re

UNVERIFIED_PREFIX = "⚠️ [Unverified]"

_HAS_URL = re.compile(r"https?://\S+")
_HAS_SOURCE_KEYWORD = re.compile(r"\bSource:\s*\S", re.IGNORECASE)
_DC_PATTERN = re.compile(
    r"\b\d+\s*(?:distribution\s*center|distribution\s*centers|dc|dcs)\b",
    re.IGNORECASE,
)
_BULLET_PREFIX = re.compile(r"^(\s*•\s*)(.*)$")
_PROPER_NOUN = re.compile(r"\b[A-Z][a-z]+\b")
_PAST_TENSE = re.compile(r"\b\w{3,}ed\b")


def has_source(line: str) -> bool:
    return bool(_HAS_URL.search(line) or _HAS_SOURCE_KEYWORD.search(line))


def is_assertion(line: str) -> bool:
    body = line
    m = _BULLET_PREFIX.match(line)
    if m:
        body = m.group(2)
    if re.search(r"\d", body):
        return True
    if _PAST_TENSE.search(body):
        return True
    if _PROPER_NOUN.search(body):
        return True
    return False


def is_unsourced_dc_count(line: str) -> bool:
    if not _DC_PATTERN.search(line):
        return False
    return not has_source(line)


def flag_if_unverified(line: str) -> str:
    if has_source(line):
        return line
    if not is_assertion(line):
        return line
    if UNVERIFIED_PREFIX in line:
        return line
    m = _BULLET_PREFIX.match(line)
    if m:
        return f"{m.group(1)}{UNVERIFIED_PREFIX} — {m.group(2)}"
    return f"{UNVERIFIED_PREFIX} — {line}"
