"""Contact pipeline (Phase 13).

Glues the Apollo contact pull to the HubSpot existence-check tagger.
Produces the shape consumed by the contact renderer:

    {"contacts": [...], "warning": Optional[str]}

Graceful degradation:
- Apollo missing or fails → empty contacts + warning ("Apollo unavailable")
- HubSpot client missing → Apollo contacts untagged + warning
  ("HubSpot tagging skipped — set HUBSPOT_ACCESS_TOKEN to enable")
- Both present → contacts flow through `tag_contacts` (Phase 7) which
  already returns the same shape, including its own warning when
  HubSpot is reachable but errors out.

Never raises.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.integrations.hubspot.contact_check import tag_contacts
from src.research.personas import map_personas_to_title_keywords
from src.research.sessions import ResearchSession
from src.research.title_filter import filter_by_persona_fit
from src.security.exception_logger import safe_log_exception

logger = logging.getLogger(__name__)

APOLLO_MISSING_WARNING = "Apollo unavailable — contacts skipped"
HUBSPOT_MISSING_WARNING = (
    "HubSpot tagging skipped — set HUBSPOT_ACCESS_TOKEN to enable"
)


def build_tagged_contacts(
    session: ResearchSession,
    apollo_client,
    hubspot_contact_client,
    portal_id: Optional[str],
) -> Dict[str, Any]:
    """Run the Apollo → HubSpot tagging pipeline for `session`.

    Returns `{"contacts": [...], "warning": Optional[str]}`.
    Never raises.
    """
    if apollo_client is None:
        return {"contacts": [], "warning": APOLLO_MISSING_WARNING}

    keywords = map_personas_to_title_keywords(session.personas or [])

    try:
        contacts = apollo_client.search_contacts_by_company_and_titles(
            session.account_name, keywords
        )
    except Exception as e:  # noqa: BLE001 — graceful fallback
        safe_log_exception(logger, e, "apollo contact search failed")
        return {"contacts": [], "warning": APOLLO_MISSING_WARNING}

    if not isinstance(contacts, list):
        contacts = []

    # Subtract Apollo's fuzzy-match false-positives (e.g. VP IT Ops on
    # an Operations Lead pull). Default-permissive on missing titles.
    contacts = filter_by_persona_fit(contacts, session.personas or [])

    if hubspot_contact_client is None:
        return {
            "contacts": list(contacts),
            "warning": HUBSPOT_MISSING_WARNING,
        }

    # tag_contacts handles HubSpot 5xx fallback internally and never raises.
    return tag_contacts(
        contacts,
        hubspot_contact_client,
        portal_id=portal_id or "",
    )
