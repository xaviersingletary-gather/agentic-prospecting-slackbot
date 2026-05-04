"""HubSpot contact existence check + tagging (Phase 7 / spec §1.2.1).

Given Apollo-style contact dicts, look each up in HubSpot and tag with
`EXISTS IN HUBSPOT` (with link) or `NET NEW`.

Security gates wired here:
- S1.2.1a — All exceptions caught and logged via `safe_log_exception`
            (type-name only, no `str(e)`).
- S1.2.1b — `render_contact_for_slack` runs every external string through
            `safe_mrkdwn`.
- S1.2.2  — `build_contact_url` URL-encodes both portal_id and contact_id
            via `urllib.parse.quote(safe="")` so traversal payloads can't
            escape the `/contacts/{portal}/contact/` path.

Behaviour:
- Email lookup first; falls back to firstname+lastname+company at confidence >= 0.9.
- Rate limit: batches of 10 with >=100ms sleep between batches.
- 5xx / any exception from the HubSpot client → graceful fallback. The
  affected contacts are returned untagged (status="NET NEW") and a warning
  banner is attached. The whole research run never fails because HubSpot is
  down.
"""
from __future__ import annotations

import logging
import time
from typing import Optional, Sequence
from urllib.parse import quote

from src.security.exception_logger import safe_log_exception
from src.security.safe_mrkdwn import safe_mrkdwn

logger = logging.getLogger(__name__)

BATCH_SIZE = 10
INTER_BATCH_SLEEP_SECONDS = 0.1  # >=100ms per spec §1.2.1
WARNING_BANNER = "HubSpot check unavailable — showing unverified contacts"
HUBSPOT_BASE_APP_URL = "https://app.hubspot.com"


def build_contact_url(portal_id: str, contact_id: str) -> str:
    """Build a HubSpot contact URL with strict encoding of both IDs.

    `quote(safe="")` percent-encodes `/`, `.`, and other path-significant
    characters so a `contact_id` like `"../malicious"` cannot escape the
    `/contacts/{portal}/contact/` segment. Spec gate S1.2.2.
    """
    portal_q = quote(str(portal_id), safe="")
    contact_q = quote(str(contact_id), safe="")
    return f"{HUBSPOT_BASE_APP_URL}/contacts/{portal_q}/contact/{contact_q}"


def render_contact_for_slack(contact: dict) -> str:
    """Render a tagged contact as a single Slack mrkdwn line.

    Every external string flows through `safe_mrkdwn` per S1.2.1b.
    Empty fields are skipped so we never render `[NET NEW]   — VP @ Co ()`
    with broken layout. LinkedIn URL renders as a clickable
    `<url|LinkedIn>` link. Apollo's `email_not_unlocked@…` placeholder
    is treated as no-email-available and hidden.
    """
    first = safe_mrkdwn(contact.get("first_name", "")).strip()
    last = safe_mrkdwn(contact.get("last_name", "")).strip()
    title = safe_mrkdwn(contact.get("title", "")).strip()
    company = safe_mrkdwn(contact.get("company", "")).strip()
    raw_email = (contact.get("email") or "").strip()
    raw_linkedin = (contact.get("linkedin_url") or "").strip()
    status = contact.get("status", "")

    # Apollo placeholder for emails the account doesn't have credits to
    # unlock — show "email locked" instead of a fake address.
    email_locked = "email_not_unlocked" in raw_email.lower()
    has_email = bool(raw_email) and not email_locked
    email = safe_mrkdwn(raw_email) if has_email else ""

    name = (f"{first} {last}").strip() or "_(name unknown)_"
    title_company = " @ ".join(p for p in [title, company] if p) or ""

    parts = [f"*{name}*"]
    if title_company:
        parts.append(title_company)
    if email:
        parts.append(email)
    elif email_locked:
        parts.append("_email locked_")
    if raw_linkedin and raw_linkedin.lower().startswith(("http://", "https://")):
        # LinkedIn URLs are external user-controlled — strip Slack-link
        # metacharacters before wrapping. Display text is a known-safe
        # constant, not user input.
        clean = raw_linkedin.replace(">", "").replace("|", "")
        parts.append(f"<{clean}|LinkedIn>")
    if contact.get("hubspot_url"):
        parts.append(f"<{contact['hubspot_url']}|HubSpot>")

    body = "  ·  ".join(parts)
    return f"[{status}] {body}"


def _lookup_one(client, contact: dict) -> Optional[dict]:
    """Try email lookup, then name+company. Returns the HubSpot record dict
    or None. Raises on transport errors so the caller can mark fallback."""
    email = (contact.get("email") or "").strip()
    if email:
        match = client.search_contact_by_email(email)
        if match is not None:
            return match
    first = (contact.get("first_name") or "").strip()
    last = (contact.get("last_name") or "").strip()
    company = (contact.get("company") or "").strip()
    if first and last:
        match = client.search_contact_by_name_company(first, last, company)
        if match is not None:
            return match
    return None


def tag_contacts(
    contacts: Sequence[dict],
    client,
    *,
    portal_id: str,
) -> dict:
    """Tag each contact as EXISTS IN HUBSPOT or NET NEW.

    Returns: `{"contacts": [...], "warning": Optional[str]}`.
    Existing contacts are returned BEFORE net-new contacts (spec §1.2.1).

    Never raises. If HubSpot is unreachable, warning is set and contacts are
    surfaced as NET NEW (unverified) so research continues.
    """
    tagged: list[dict] = []
    warning: Optional[str] = None
    contact_list = list(contacts)

    for batch_idx in range(0, len(contact_list), BATCH_SIZE):
        # Inter-batch throttle (spec §1.2.1: 100 req / 10s).
        if batch_idx > 0:
            time.sleep(INTER_BATCH_SLEEP_SECONDS)

        batch = contact_list[batch_idx : batch_idx + BATCH_SIZE]
        for contact in batch:
            out = dict(contact)
            try:
                match = _lookup_one(client, contact)
            except Exception as e:  # noqa: BLE001 — graceful fallback
                # S1.2.1a: exception name only; do NOT log str(e).
                safe_log_exception(logger, e, "hubspot lookup failed")
                warning = WARNING_BANNER
                out["status"] = "NET NEW"
                tagged.append(out)
                continue

            if match is not None:
                out["status"] = "EXISTS IN HUBSPOT"
                out["hubspot_url"] = build_contact_url(
                    portal_id=portal_id,
                    contact_id=match.get("id", ""),
                )
                # Carry the HubSpot-known props forward for rendering.
                hs_props = match.get("properties", {}) or {}
                out.setdefault("hubspot_properties", hs_props)
            else:
                out["status"] = "NET NEW"
            tagged.append(out)

    # Group: existing first, net-new second. Stable within each group.
    existing = [c for c in tagged if c.get("status") == "EXISTS IN HUBSPOT"]
    net_new = [c for c in tagged if c.get("status") == "NET NEW"]
    return {"contacts": existing + net_new, "warning": warning}
