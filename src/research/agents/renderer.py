"""Render an AccountResearch blob into Slack Block Kit.

Two responsibilities:
  1. `render_research_blocks(blob)` — turn the assembled blob into a
     flat list of Block Kit dicts: timestamp header + 8 section
     headers + per-claim mrkdwn lines with source-tag chips.
  2. `chunk_blocks_for_slack(blocks)` — split a long block list into
     multiple chat.postMessage payloads when the cumulative text
     exceeds Slack's per-message character limit. Continuation markers
     are added to the leading section block of each chunk after the
     first.

Slack output safety:
- Every interpolated string passes through `safe_mrkdwn` so an
  attacker-controlled snippet (poisoned Exa result, prompt-injected
  LLM output) can't plant phishing-style links.
- Source URLs are NOT escaped — they're rendered as `<url|label>` and
  must remain clickable. They've already passed `assert_safe_url`
  upstream in each agent.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from src.research.agents.aggregator import format_timestamp_for_slack
from src.research.agents.contract import AGENT_SECTIONS
from src.security.safe_mrkdwn import safe_mrkdwn

# Slack's hard limit on the text inside a single section block is 3000
# chars; the message itself caps at ~40k. We chunk well under the
# message cap so reps see content fast, marked with continuation.
_MAX_CHARS_PER_MESSAGE = 2800
_MAX_CLAIMS_PER_SECTION = 8

# Per-block hard cap. Slack rejects sections whose `text.text` field
# exceeds 3000 chars with `invalid_blocks`. We pack content into multiple
# section blocks per section when needed; 2700 leaves headroom for any
# trailing markers we append.
_MAX_CHARS_PER_SECTION_BLOCK = 2700

_TAG_LABELS = {
    "public": ":globe_with_meridians: public",
    "inferred": ":mag: inferred",
    "internal": ":lock: internal",
    "not_found": ":grey_question: not found",
    "error": ":warning: error",
}


def render_research_blocks(blob: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Render the assembled research blob into Block Kit.

    Output shape (no chunking yet):
      [
        section: timestamp + account name,
        divider,
        section: <Section 1 title>,
        section: <claims for Section 1>,
        divider,
        ... repeat for each agent ...
      ]
    """
    account = safe_mrkdwn(blob.get("account_name") or "(unknown)")
    intent = blob.get("intent")
    intent_label = safe_mrkdwn(_humanize_intent(intent)) if intent else ""

    ts_line = format_timestamp_for_slack(blob.get("generated_at") or "")
    header_lines: List[str] = [f"*:office: {account}*", f"_{ts_line}_"]
    if intent_label:
        header_lines.append(f"_Intent: {intent_label}_")

    blocks: List[Dict[str, Any]] = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "\n".join(header_lines)},
        },
        {"type": "divider"},
    ]

    for agent_payload in blob.get("agents") or []:
        blocks.extend(_render_section(agent_payload))
        blocks.append({"type": "divider"})

    # Drop the trailing divider — looks cleaner.
    if blocks and blocks[-1].get("type") == "divider":
        blocks.pop()

    return blocks


def _render_section(agent_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """One agent's payload → header section + claims section.

    Section title is taken from the canonical `AGENT_SECTIONS` map by
    agent_name — NOT from the payload — so attacker-controlled agent
    output can't smuggle mrkdwn into the section header. The hardcoded
    map is trusted; sanitizing it would mangle legitimate punctuation
    like "Shrink & Compliance".
    """
    agent_name = agent_payload.get("agent_name") or ""
    section_title = AGENT_SECTIONS.get(
        agent_name, safe_mrkdwn(agent_payload.get("section_title") or "(untitled)")
    )
    claims = list(agent_payload.get("claims") or [])

    blocks: List[Dict[str, Any]] = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*{section_title}*"},
        }
    ]

    if not claims:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "_No findings produced._",
                },
            }
        )
        return blocks

    lines: List[str] = []
    for claim in claims[:_MAX_CLAIMS_PER_SECTION]:
        lines.append(_render_claim_line(claim))
    if len(claims) > _MAX_CLAIMS_PER_SECTION:
        lines.append(
            f"_…{len(claims) - _MAX_CLAIMS_PER_SECTION} additional "
            f"finding(s) elided; ask in-thread for more._"
        )

    # Pack lines into one or more section blocks, each capped at
    # _MAX_CHARS_PER_SECTION_BLOCK. Slack rejects any single section
    # whose text exceeds 3000 chars (`invalid_blocks`). When a single
    # line is itself oversized, hard-truncate it inline.
    for body in _pack_lines_into_section_bodies(lines):
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": body},
            }
        )
    return blocks


def _pack_lines_into_section_bodies(lines: List[str]) -> List[str]:
    """Group rendered claim lines into section bodies under the per-block cap.

    Each body is at most `_MAX_CHARS_PER_SECTION_BLOCK` chars including
    the inter-line newlines. A single line longer than the cap is
    truncated to `cap - 24` chars with a "…(truncated)" marker so it
    still passes Slack's validator.
    """
    cap = _MAX_CHARS_PER_SECTION_BLOCK
    bodies: List[str] = []
    current: List[str] = []
    current_len = 0
    for line in lines:
        # Hard-truncate any single line that exceeds the per-block cap.
        if len(line) > cap:
            line = line[: cap - 24] + "… _(truncated)_"
        line_len = len(line) + (1 if current else 0)  # +1 for newline
        if current and current_len + line_len > cap:
            bodies.append("\n".join(current))
            current = [line]
            current_len = len(line)
        else:
            current.append(line)
            current_len += line_len
    if current:
        bodies.append("\n".join(current))
    return bodies or [""]


def _render_claim_line(claim: Dict[str, Any]) -> str:
    """One claim → one bullet line with tag chip + body + source link."""
    tag = claim.get("source_tag") or "not_found"
    tag_label = _TAG_LABELS.get(tag, f":grey_question: {tag}")
    text = safe_mrkdwn(claim.get("text") or "")

    suffix_bits: List[str] = []
    url = claim.get("source_url")
    if url:
        # Keep the URL as-is — it has been validated upstream.
        suffix_bits.append(f"<{url}|source>")
    src = claim.get("source")
    if src:
        suffix_bits.append(f"_({safe_mrkdwn(src)})_")
    date = claim.get("date")
    if date:
        suffix_bits.append(f"_{safe_mrkdwn(_humanize_date(str(date)))}_")
    inference = claim.get("inference_logic")
    if inference:
        # Collapse newlines / runs of whitespace into a single line: Slack
        # mrkdwn italics (`_..._`) cannot span newlines, so a multi-line
        # basis (e.g. the Exception Tax `math_shown`) would otherwise leak
        # stray underscores and break the styling mid-block.
        flat = " ".join(str(inference).split())
        suffix_bits.append(f"_basis: {safe_mrkdwn(flat)}_")

    suffix = " — " + " ".join(suffix_bits) if suffix_bits else ""
    return f"• [{tag_label}] {text}{suffix}"


def _humanize_date(date: str) -> str:
    """Turn an Exa-style ISO-8601 timestamp into a compact `Mon YYYY` label.

    Exa returns `published_date` as a full ISO string (e.g.
    `2021-04-14T00:00:00.000Z`). Rendered raw it leaks `.000Z` noise into
    the Slack output. We parse it to `Apr 2021`; anything we can't parse
    falls back to the original string so non-ISO dates still render.
    """
    raw = date.strip()
    if not raw:
        return raw
    # Normalise a trailing `Z` to an offset Python's parser accepts, and
    # drop fractional seconds the stdlib parser is picky about.
    candidate = raw.replace("Z", "+00:00")
    for parse in (
        lambda s: datetime.fromisoformat(s),
        lambda s: datetime.strptime(s[:10], "%Y-%m-%d"),
    ):
        try:
            return parse(candidate).strftime("%b %Y")
        except (ValueError, TypeError):
            continue
    return raw


def _humanize_intent(intent: str) -> str:
    return {
        "prospecting": "Prospecting",
        "meeting_prep": "Meeting Prep",
        "asset_building": "Asset Building",
        "general_research": "General Research",
    }.get(intent, intent.replace("_", " ").title())


def _block_char_length(block: Dict[str, Any]) -> int:
    """Rough char length for chunking. Counts the text payload only."""
    text_obj = block.get("text") or {}
    if isinstance(text_obj, dict):
        return len(text_obj.get("text") or "")
    return 0


def chunk_blocks_for_slack(
    blocks: List[Dict[str, Any]],
    *,
    max_chars: int = _MAX_CHARS_PER_MESSAGE,
) -> List[List[Dict[str, Any]]]:
    """Split a block list into chunks each under `max_chars` total text.

    Chunks 2…N have a small "_continued (N/M)_" marker prepended so reps
    know multiple posts represent a single research run. We split on
    section boundaries — never inside a section — so dividers stay
    meaningful.

    Always returns at least one chunk; if a single block exceeds
    `max_chars` we return it on its own (best-effort; Slack will
    truncate inside its own 3000-char cap if the section text itself
    is too long, but we can't help that without semantic splitting).
    """
    if not blocks:
        return [[]]

    chunks: List[List[Dict[str, Any]]] = [[]]
    cur_len = 0
    for block in blocks:
        blen = _block_char_length(block)
        # If this block alone is bigger than the cap, dump the current
        # chunk and put this oversized block in its own.
        if blen > max_chars and chunks[-1]:
            chunks.append([])
            cur_len = 0
        # Otherwise, if adding it would push us over, start a new chunk
        # on the next divider-or-section boundary.
        if cur_len + blen > max_chars and chunks[-1]:
            chunks.append([])
            cur_len = 0
        chunks[-1].append(block)
        cur_len += blen

    # Drop empty chunks (defensive — shouldn't happen with the logic above).
    chunks = [c for c in chunks if c]

    if len(chunks) <= 1:
        return chunks

    # Prepend continuation markers to chunks 2..N.
    total = len(chunks)
    for i in range(1, total):
        marker = {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"_…continued ({i + 1}/{total})_",
            },
        }
        chunks[i].insert(0, marker)

    return chunks
