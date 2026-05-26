"""Agent 8 — Contacts (May 26 V1 spec §6).

Wraps the existing Apollo + HubSpot tagging pipeline into the new agent
contract. Each surfaced contact becomes one Claim:
  - PUBLIC if Apollo provided the contact and HubSpot has NO existing
    record for it (cold contact; no internal relationship)
  - INTERNAL if HubSpot already has a contact / deal / engagement on it
    (existing relationship, prior touch history)
  - NOT_FOUND if Apollo + HubSpot both returned nothing for the account
  - ERROR if the upstream pipeline failed catastrophically

Salesforce is intentionally NOT wired here — Xavier deferred SF to V2.
The spec calls for Salesforce ownership enrichment; we mark that as a
known gap in `notes` so the aggregator can surface it.

Trust posture mirrors `contact_pipeline.build_tagged_contacts`:
- `build_tagged_contacts` already never raises and returns a structured
  `{"contacts": [...], "warning": ...}` dict.
- We add a defensive try/except around it anyway so a change in that
  contract can't escalate to a research-run failure.
- No URLs from external systems land here; HubSpot record links are
  internal app URLs (handled by the renderer downstream).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from src.research.agents.contract import AgentResult, Claim, SourceTag
from src.research.contact_pipeline import build_tagged_contacts
from src.research.sessions import ResearchSession
from src.security.exception_logger import safe_log_exception

logger = logging.getLogger(__name__)

AGENT_NAME = "agent_8_contacts"
SECTION_TITLE = "Contacts"

# Cap per call so the section stays scannable in Slack.
_MAX_CONTACT_CLAIMS = 12


@dataclass
class AgentContext:
    """Agent 8 needs a fully-formed ResearchSession because the existing
    contact pipeline keys off `session.personas` and `session.account_name`.

    `apollo_client`, `hubspot_contact_client`, `hubspot_portal_id` are
    pass-throughs to `build_tagged_contacts`. Tests inject mocks; the
    dispatcher pulls real clients from `src.research.clients_factory`.
    """

    session: ResearchSession
    apollo_client: Any = None
    hubspot_contact_client: Any = None
    hubspot_portal_id: Optional[str] = None


def _contact_summary(contact: Dict[str, Any]) -> str:
    """One-line label combining name + title + department + ownership."""
    name = (contact.get("name") or "").strip()
    title = (contact.get("title") or "").strip()
    dept = (contact.get("department") or "").strip()
    bits = []
    if name:
        bits.append(name)
    if title:
        bits.append(title)
    if dept and dept.lower() not in (title or "").lower():
        bits.append(dept)
    if contact.get("owner"):
        bits.append(f"owner: {contact['owner']}")
    if contact.get("prior_touch"):
        bits.append("prior touch")
    return " — ".join(bits) if bits else "(unnamed contact)"


def _claim_for_contact(contact: Dict[str, Any]) -> Optional[Claim]:
    """One contact → one Claim. Internal if HubSpot tagged it as existing."""
    text = _contact_summary(contact)
    if not text:
        return None

    has_internal_signal = bool(
        contact.get("hubspot_contact_id")
        or contact.get("hubspot_deal_id")
        or contact.get("owner")
        or contact.get("prior_touch")
    )

    if has_internal_signal:
        source_bits = []
        if contact.get("hubspot_contact_id"):
            source_bits.append(f"HubSpot contact {contact['hubspot_contact_id']}")
        if contact.get("hubspot_deal_id"):
            source_bits.append(f"HubSpot deal {contact['hubspot_deal_id']}")
        if not source_bits:
            source_bits.append("HubSpot")
        return Claim(
            text=text,
            source_tag=SourceTag.INTERNAL,
            source="; ".join(source_bits),
        )

    # Apollo-only contact → PUBLIC. Apollo profile URL is the source.
    url = contact.get("linkedin_url") or contact.get("apollo_profile_url")
    if not url:
        # Without a URL we can't satisfy the PUBLIC field requirement.
        # Surface as NOT_FOUND with the name in text so the rep still
        # sees a candidate.
        return Claim(
            text=f"{text} (no public profile URL captured)",
            source_tag=SourceTag.NOT_FOUND,
        )
    return Claim(
        text=text,
        source_tag=SourceTag.PUBLIC,
        source_url=url,
    )


async def run(ctx: AgentContext) -> AgentResult:
    session = ctx.session
    if session is None or not getattr(session, "account_name", None):
        return AgentResult.error(
            AGENT_NAME, SECTION_TITLE, "Missing session / account_name"
        )

    try:
        tagged = build_tagged_contacts(
            session=session,
            apollo_client=ctx.apollo_client,
            hubspot_contact_client=ctx.hubspot_contact_client,
            portal_id=ctx.hubspot_portal_id,
        )
    except Exception as e:  # noqa: BLE001
        safe_log_exception(logger, e, f"{AGENT_NAME} build_tagged_contacts failed")
        return AgentResult.error(
            AGENT_NAME, SECTION_TITLE, "Contact pipeline failed."
        )

    contacts: List[Dict[str, Any]] = list(tagged.get("contacts") or [])
    warning = tagged.get("warning")

    claims: List[Claim] = []
    for contact in contacts[:_MAX_CONTACT_CLAIMS]:
        claim = _claim_for_contact(contact)
        if claim is not None:
            claims.append(claim)

    if not claims:
        msg = "No Apollo or HubSpot contacts surfaced for this account."
        if warning:
            msg += f" ({warning})"
        claims = [Claim(text=msg, source_tag=SourceTag.NOT_FOUND)]

    notes = warning if warning else None
    # Spec calls for Salesforce ownership enrichment; we deferred SF to V2.
    sf_note = "Salesforce ownership enrichment deferred to V2."
    notes = f"{notes}; {sf_note}" if notes else sf_note

    return AgentResult(
        agent_name=AGENT_NAME,
        section_title=SECTION_TITLE,
        claims=claims,
        notes=notes,
    )
