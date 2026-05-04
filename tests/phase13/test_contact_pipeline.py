"""Phase 13 — contact_pipeline.

Glue between Apollo (raw contact pull) and HubSpot tagging. Returns a
shape compatible with the existing renderer:
    {"contacts": [...], "warning": Optional[str]}
"""
from unittest.mock import MagicMock

import pytest


def _session(rep_id="U_REP", account="Kroger", personas=None):
    from src.research.sessions import create_session

    s = create_session(rep_id=rep_id, account_name=account)
    s.personas = personas or ["operations_lead"]
    return s


@pytest.fixture(autouse=True)
def _reset_sessions():
    from src.research import sessions
    sessions._reset_for_tests()
    yield
    sessions._reset_for_tests()


def test_apollo_ok_hubspot_ok_returns_tagged_contacts(mocker):
    from src.research.contact_pipeline import build_tagged_contacts

    raw_contacts = [
        {"first_name": "Jane", "last_name": "Doe",
         "email": "jane@kroger.com", "title": "VP Warehouse",
         "company": "Kroger"},
    ]

    apollo = MagicMock()
    apollo.search_contacts_by_company_and_titles.return_value = raw_contacts

    hs = MagicMock()
    # tag_contacts will call client.search_contact_by_email — return None so
    # contact ends up tagged NET NEW (covers the wiring without needing a
    # full HubSpot fixture)
    hs.search_contact_by_email.return_value = None
    hs.search_contact_by_name_company.return_value = None

    s = _session()
    result = build_tagged_contacts(
        s, apollo_client=apollo, hubspot_contact_client=hs, portal_id="12345"
    )

    assert "contacts" in result
    assert "warning" in result
    assert len(result["contacts"]) == 1
    assert result["contacts"][0]["status"] == "NET NEW"
    apollo.search_contacts_by_company_and_titles.assert_called_once()


def test_apollo_called_with_persona_keywords(mocker):
    from src.research.contact_pipeline import build_tagged_contacts

    apollo = MagicMock()
    apollo.search_contacts_by_company_and_titles.return_value = []
    hs = MagicMock()

    s = _session(personas=["operations_lead"])
    build_tagged_contacts(
        s, apollo_client=apollo, hubspot_contact_client=hs, portal_id="12345"
    )

    args, kwargs = apollo.search_contacts_by_company_and_titles.call_args
    # Account name is positional first arg; keywords second
    company = args[0] if args else kwargs.get("company_name")
    keywords = args[1] if len(args) > 1 else kwargs.get("title_keywords")
    assert company == "Kroger"
    # Operations Lead maps to ops/warehouse/distribution/inventory titles.
    assert "VP Operations" in keywords
    assert "Director of Warehouse" in keywords


def test_apollo_ok_hubspot_none_returns_untagged_contacts_with_warning(mocker):
    from src.research.contact_pipeline import build_tagged_contacts

    raw_contacts = [
        {"first_name": "Jane", "last_name": "Doe",
         "email": "jane@kroger.com", "title": "VP Warehouse",
         "company": "Kroger"},
    ]

    apollo = MagicMock()
    apollo.search_contacts_by_company_and_titles.return_value = raw_contacts

    s = _session()
    result = build_tagged_contacts(
        s, apollo_client=apollo, hubspot_contact_client=None, portal_id=None
    )

    assert len(result["contacts"]) == 1
    assert result["warning"] is not None
    assert "HubSpot" in result["warning"]
    # Contact is returned untagged (no status="EXISTS IN HUBSPOT")
    assert result["contacts"][0].get("status") != "EXISTS IN HUBSPOT"


def test_apollo_none_returns_empty_with_warning():
    from src.research.contact_pipeline import build_tagged_contacts

    s = _session()
    result = build_tagged_contacts(
        s, apollo_client=None, hubspot_contact_client=None, portal_id=None
    )

    assert result["contacts"] == []
    assert result["warning"] is not None
    assert "Apollo" in result["warning"]


def test_apollo_returns_empty_list_treated_as_no_contacts():
    """If Apollo returns [] (empty result, not failure), no warning needed."""
    from src.research.contact_pipeline import build_tagged_contacts

    apollo = MagicMock()
    apollo.search_contacts_by_company_and_titles.return_value = []

    s = _session()
    result = build_tagged_contacts(
        s, apollo_client=apollo, hubspot_contact_client=None, portal_id=None
    )

    assert result["contacts"] == []
    # Apollo client present + returned [] is the "no matches" case, not a
    # failure. Warning may still be set for HubSpot absence — that's OK.


def test_tag_contacts_invoked_when_both_clients_present(mocker):
    """tag_contacts is the seam that does the existence check + URL build.
    It must be called when both clients exist, NOT called otherwise."""
    spy = mocker.patch("src.research.contact_pipeline.tag_contacts",
                       return_value={"contacts": [], "warning": None})

    apollo = MagicMock()
    apollo.search_contacts_by_company_and_titles.return_value = [
        {"first_name": "x", "last_name": "y", "email": "a@b.com",
         "company": "c", "title": "t"},
    ]
    hs = MagicMock()

    from src.research.contact_pipeline import build_tagged_contacts
    s = _session()
    build_tagged_contacts(
        s, apollo_client=apollo, hubspot_contact_client=hs, portal_id="1"
    )
    assert spy.called


def test_tag_contacts_NOT_invoked_when_hubspot_client_missing(mocker):
    spy = mocker.patch("src.research.contact_pipeline.tag_contacts",
                       return_value={"contacts": [], "warning": None})

    apollo = MagicMock()
    apollo.search_contacts_by_company_and_titles.return_value = [
        {"first_name": "x", "last_name": "y", "email": "a@b.com"},
    ]

    from src.research.contact_pipeline import build_tagged_contacts
    s = _session()
    build_tagged_contacts(
        s, apollo_client=apollo, hubspot_contact_client=None, portal_id=None
    )
    spy.assert_not_called()
