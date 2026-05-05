"""Persona-fit title filter (V1.2.x — Apollo title precision).

Apollo's `person_titles` filter is fuzzy: asking for "VP Operations"
returns VP IT Operations, VP Sales Ops, VP Revenue Ops, etc. — every
contact with "Operations" in the title regardless of domain. This
module subtracts the false positives by checking each title against
the selected personas' `negative_keywords` list.

Design choices:
- Apollo already did the positive matching. We only filter OUT noise,
  we don't re-score positives. Keeps the logic boringly predictable.
- Negative match uses word-boundary regex (`\\bIT\\b`) so "IT" hits
  "VP IT Operations" but doesn't false-positive on "Bit", "Fitness".
- Multi-persona: keep if ANY selected persona accepts. Necessary for
  the case where compliance_lead allows "IT" but operations_lead
  excludes it — selecting both should keep IT directors.
- Empty/missing title → KEEP. No signal to filter on; Apollo already
  decided the contact was relevant. Default-permissive on missing data.
- Empty persona list → KEEP all (no-op). Caller hasn't expressed intent.

Logged at INFO level so we can see drop rates in Railway.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Iterable, List, Sequence

from src.research.personas import PERSONAS

logger = logging.getLogger(__name__)


def _has_word(text: str, term: str) -> bool:
    """Case-insensitive word-boundary match."""
    pattern = r"\b" + re.escape(term) + r"\b"
    return re.search(pattern, text, re.IGNORECASE) is not None


def _matches_any(text: str, terms: Iterable[str]) -> bool:
    return any(_has_word(text, t) for t in terms)


def _persona_accepts(title: str, persona_key: str) -> bool:
    """True if `persona_key` would accept this title (no negative hit).

    Unknown persona key returns False so it doesn't accidentally
    rescue a contact via permissive default — the caller's intent was
    a specific persona, not "anyone".
    """
    cfg = PERSONAS.get(persona_key)
    if cfg is None:
        return False
    negatives = cfg.get("negative_keywords") or []
    return not _matches_any(title, negatives)


def filter_by_persona_fit(
    contacts: Sequence[Dict[str, Any]],
    persona_keys: Sequence[str],
) -> List[Dict[str, Any]]:
    """Return only contacts whose title fits at least one selected persona.

    Behaviour:
    - Empty persona_keys → return all contacts unchanged (no-op).
    - Empty/missing title on a contact → keep (no signal to filter on).
    - All selected personas reject the title → drop.
    - At least one persona accepts → keep.

    Never raises. Logs the drop count.
    """
    contacts_in = list(contacts)
    if not persona_keys:
        return contacts_in

    # Drop unknown personas up-front so the per-contact loop has a
    # stable allowlist; if the filter is left with zero valid personas,
    # we return the input unchanged rather than dropping everything.
    valid_keys = [k for k in persona_keys if k in PERSONAS]
    if not valid_keys:
        return contacts_in

    kept: List[Dict[str, Any]] = []
    dropped_titles: List[str] = []
    for contact in contacts_in:
        title = (contact.get("title") or "").strip()
        if not title:
            kept.append(contact)
            continue
        if any(_persona_accepts(title, k) for k in valid_keys):
            kept.append(contact)
        else:
            dropped_titles.append(title)

    if dropped_titles:
        logger.info(
            "[title_filter] dropped %d/%d contacts (personas=%s); "
            "sample dropped titles=%r",
            len(dropped_titles), len(contacts_in), list(valid_keys),
            dropped_titles[:5],
        )
    return kept
