"""Phase 20 — Agent 8 (Contacts) tests.

Wraps build_tagged_contacts; tests inject the apollo/hubspot mocks via
AgentContext rather than monkey-patching modules.

Cases:
  - Mix of HubSpot-tagged + Apollo-only contacts → mix of INTERNAL + PUBLIC
  - Apollo-only (no HubSpot) → PUBLIC contacts
  - Both empty → NOT_FOUND
  - Apollo raises (build_tagged_contacts surfaces empty + warning) → NOT_FOUND
    with warning surfaced in notes
  - Apollo contact missing linkedin/apollo URL → NOT_FOUND for that contact
  - notes always mentions Salesforce V2 deferral
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.research.agents.agent_8_contacts import AgentContext, run
from src.research.agents.contract import SourceTag
from src.research.sessions import ResearchSession


pytestmark = pytest.mark.asyncio


def _session(account="Walmart", personas=None):
    return ResearchSession(
        session_id="sess-1",
        rep_id="U_REP",
        account_name=account,
        personas=personas or ["technical_lead", "operations_lead"],
    )


def _internal_contact(**overrides):
    base = {
        "name": "Jane Ops",
        "title": "VP Operations",
        "department": "Operations",
        "linkedin_url": "https://linkedin.com/in/janeops",
        "hubspot_contact_id": "12345",
        "owner": "rep@gather.ai",
        "prior_touch": True,
    }
    base.update(overrides)
    return base


def _apollo_contact(**overrides):
    base = {
        "name": "Carl Cold",
        "title": "Director CI",
        "department": "Continuous Improvement",
        "linkedin_url": "https://linkedin.com/in/carlcold",
    }
    base.update(overrides)
    return base


async def test_mix_of_hubspot_and_apollo_contacts(mocker):
    mocker.patch(
        "src.research.agents.agent_8_contacts.build_tagged_contacts",
        return_value={
            "contacts": [_internal_contact(), _apollo_contact()],
            "warning": None,
        },
    )
    result = await run(AgentContext(session=_session()))

    internal = [c for c in result.claims if c.source_tag is SourceTag.INTERNAL]
    public = [c for c in result.claims if c.source_tag is SourceTag.PUBLIC]
    assert len(internal) == 1
    assert len(public) == 1
    assert "Jane Ops" in internal[0].text
    assert "HubSpot contact 12345" in (internal[0].source or "")
    assert "Carl Cold" in public[0].text
    assert public[0].source_url == "https://linkedin.com/in/carlcold"


async def test_apollo_only_when_hubspot_missing_emits_public(mocker):
    mocker.patch(
        "src.research.agents.agent_8_contacts.build_tagged_contacts",
        return_value={
            "contacts": [_apollo_contact(name="A"), _apollo_contact(name="B")],
            "warning": "HubSpot tagging skipped — set HUBSPOT_ACCESS_TOKEN to enable",
        },
    )
    result = await run(AgentContext(session=_session()))
    public = [c for c in result.claims if c.source_tag is SourceTag.PUBLIC]
    assert len(public) == 2
    # Warning surfaces in notes.
    assert "HubSpot tagging skipped" in (result.notes or "")
    # Salesforce V2 deferral always noted.
    assert "Salesforce" in (result.notes or "")


async def test_empty_contact_list_emits_not_found(mocker):
    mocker.patch(
        "src.research.agents.agent_8_contacts.build_tagged_contacts",
        return_value={"contacts": [], "warning": None},
    )
    result = await run(AgentContext(session=_session()))
    assert len(result.claims) == 1
    assert result.claims[0].source_tag is SourceTag.NOT_FOUND


async def test_apollo_warning_surfaces_when_pipeline_returns_apollo_unavailable(mocker):
    mocker.patch(
        "src.research.agents.agent_8_contacts.build_tagged_contacts",
        return_value={
            "contacts": [],
            "warning": "Apollo unavailable — contacts skipped",
        },
    )
    result = await run(AgentContext(session=_session()))
    assert result.claims[0].source_tag is SourceTag.NOT_FOUND
    assert "Apollo unavailable" in result.claims[0].text


async def test_apollo_contact_without_url_becomes_not_found_for_that_row(mocker):
    mocker.patch(
        "src.research.agents.agent_8_contacts.build_tagged_contacts",
        return_value={
            "contacts": [_apollo_contact(linkedin_url=None, apollo_profile_url=None)],
            "warning": None,
        },
    )
    result = await run(AgentContext(session=_session()))
    # Single NOT_FOUND for the urlless Apollo contact — not a PUBLIC claim
    # without a source_url (which would have raised).
    nf = [c for c in result.claims if c.source_tag is SourceTag.NOT_FOUND]
    assert len(nf) == 1
    assert "no public profile URL" in nf[0].text


async def test_build_tagged_contacts_unexpected_raise_returns_error(mocker, caplog):
    mocker.patch(
        "src.research.agents.agent_8_contacts.build_tagged_contacts",
        side_effect=RuntimeError("downstream broke"),
    )
    with caplog.at_level("ERROR"):
        result = await run(AgentContext(session=_session()))

    assert len(result.claims) == 1
    assert result.claims[0].source_tag is SourceTag.ERROR
    log_text = "\n".join(r.message for r in caplog.records)
    assert "RuntimeError" in log_text
    assert "downstream broke" not in log_text


async def test_missing_session_returns_error():
    result = await run(AgentContext(session=None))
    assert result.claims[0].source_tag is SourceTag.ERROR


async def test_notes_always_mentions_salesforce_v2_deferral(mocker):
    mocker.patch(
        "src.research.agents.agent_8_contacts.build_tagged_contacts",
        return_value={"contacts": [_internal_contact()], "warning": None},
    )
    result = await run(AgentContext(session=_session()))
    assert "Salesforce" in (result.notes or "")
    assert "V2" in (result.notes or "")
