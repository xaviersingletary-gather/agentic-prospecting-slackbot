"""Slack Block Kit renderer for the V1.2.x reach-out-angle card.

Input shape (from `angle_builder.build_angles`):
    {
      "account_angle":          str,
      "persona_angles":         {persona_key: str, ...},
      "existing_contact_notes": [{"contact_index": int, "note": str}],
    }

Plus the `tag_result` (so we can resolve `contact_index` back into a
display name for the existing-contact notes).

Layout:
    🎯 ANGLE
    {account_angle}

    By persona
    • Operations Lead → {persona_angles.operations_lead}
    • Technical Lead  → {persona_angles.technical_lead}

    Existing relationships
    • Mike Chen — {note for index 0}

Empty/missing fields render as graceful skips. If everything is empty,
returns [] so the runner can omit the card entirely.

Security gate S1.2.4 — every external string flows through `safe_mrkdwn`.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.research.personas import PERSONAS
from src.security.safe_mrkdwn import safe_mrkdwn


def build_angle_blocks(
    angles: Dict[str, Any],
    tag_result: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    if not angles:
        return []

    account_angle = (angles.get("account_angle") or "").strip()
    persona_angles = angles.get("persona_angles") or {}
    contact_notes = angles.get("existing_contact_notes") or []

    if not account_angle and not persona_angles and not contact_notes:
        return []

    contacts = list((tag_result or {}).get("contacts") or [])
    existing = [c for c in contacts if c.get("status") == "EXISTS IN HUBSPOT"]

    blocks: List[Dict[str, Any]] = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*🎯 ANGLE*"},
        }
    ]

    if account_angle:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": safe_mrkdwn(account_angle),
            },
        })

    persona_lines = _render_persona_lines(persona_angles)
    if persona_lines:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*By persona*\n" + "\n".join(persona_lines),
            },
        })

    contact_lines = _render_contact_lines(contact_notes, existing)
    if contact_lines:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*Existing relationships*\n" + "\n".join(contact_lines),
            },
        })

    return blocks


def _render_persona_lines(persona_angles: Dict[str, Any]) -> List[str]:
    lines: List[str] = []
    # Iterate the canonical persona order so output is stable regardless
    # of how the model serialized the dict.
    for key, cfg in PERSONAS.items():
        text = persona_angles.get(key)
        if not isinstance(text, str) or not text.strip():
            continue
        # Persona label is a known constant — safe to render as bold.
        # The angle text is model output → safe_mrkdwn.
        label = cfg["label"].split(" — ", 1)[0]
        lines.append(
            f"•  *{safe_mrkdwn(label)}*  →  {safe_mrkdwn(text.strip())}"
        )
    return lines


def _render_contact_lines(
    notes: List[Dict[str, Any]], existing: List[Dict[str, Any]]
) -> List[str]:
    lines: List[str] = []
    for note_obj in notes:
        if not isinstance(note_obj, dict):
            continue
        idx = note_obj.get("contact_index")
        note = note_obj.get("note")
        if not isinstance(idx, int) or not isinstance(note, str):
            continue
        if idx < 0 or idx >= len(existing):
            continue
        contact = existing[idx]
        first = (contact.get("first_name") or "").strip()
        last = (contact.get("last_name") or "").strip()
        name = (f"{first} {last}").strip() or "(name unknown)"
        lines.append(
            f"•  *{safe_mrkdwn(name)}*  —  {safe_mrkdwn(note.strip())}"
        )
    return lines
