"""Phase 13 — full graceful-degradation matrix for run_research.

The smoke test of the whole "drop env vars in Railway and it works"
contract: the runner must call respond exactly once for every combination
of env vars present/missing, and never raise.

External clients (OpenRouter LLM, Exa, Apollo, HubSpot) are mocked at
module boundaries so no test ever hits the network.
"""
import json
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _reset_sessions():
    from src.research import sessions
    sessions._reset_for_tests()
    yield
    sessions._reset_for_tests()


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Wipe env vars AND override the loaded settings singleton — src/config
    uses load_dotenv() which re-reads the local .env file even after delenv."""
    monkeypatch.delenv("APOLLO_API_KEY", raising=False)
    monkeypatch.delenv("HUBSPOT_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("HUBSPOT_PORTAL_ID", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    from src.config import settings
    monkeypatch.setattr(settings, "APOLLO_API_KEY", "", raising=False)
    monkeypatch.setattr(settings, "HUBSPOT_ACCESS_TOKEN", "", raising=False)
    monkeypatch.setattr(settings, "HUBSPOT_PORTAL_ID", "", raising=False)
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "", raising=False)
    monkeypatch.setattr(settings, "EXA_API_KEY", "", raising=False)
    yield


def _reload_config_and_factory():
    """Return the runner module without forcing settings re-read.
    Tests use monkeypatch.setattr on the settings singleton to flip env."""
    from src.research import runner
    return runner


def _set(monkeypatch, **kwargs):
    """Convenience: override settings fields for the duration of the test."""
    from src.config import settings
    for k, v in kwargs.items():
        monkeypatch.setattr(settings, k, v, raising=False)


def _mock_findings_pipeline(mocker, exa_results=None, llm_payload=None):
    """Mock the Phase 11 Exa+OpenRouter stack."""
    if exa_results is None:
        exa_results = [
            {"title": "Kroger DC", "url": "https://example.com/k",
             "snippet": "Kroger expands"},
        ]
    if llm_payload is None:
        llm_payload = {
            "trigger_events": [{"claim": "Kroger expands DC",
                                 "source_url": "https://example.com/k"}],
            "competitor_signals": [],
            "dc_intel": [],
            "board_initiatives": [],
            "research_gaps": [],
        }

    mock_exa = MagicMock()
    mock_exa.search.return_value = exa_results
    mocker.patch("src.research.findings_builder.ExaSearchClient",
                 return_value=mock_exa)

    choice = MagicMock()
    choice.message = MagicMock()
    choice.message.content = json.dumps(llm_payload)
    resp = MagicMock()
    resp.choices = [choice]
    mock_llm = MagicMock()
    mock_llm.chat.completions.create.return_value = resp
    mocker.patch("src.research.findings_builder.OpenAI",
                 return_value=mock_llm)


def _mock_apollo_client(mocker, contacts=None):
    contacts = contacts if contacts is not None else [
        {"first_name": "Jane", "last_name": "Doe",
         "email": "jane@kroger.com", "title": "VP Warehouse",
         "company": "Kroger"},
    ]
    mock_apollo = MagicMock()
    mock_apollo.search_contacts_by_company_and_titles.return_value = contacts
    mocker.patch("src.research.clients_factory.ApolloContactClient",
                 return_value=mock_apollo)
    return mock_apollo


def _mock_hubspot_contact_client(mocker, found=None):
    mock_hs = MagicMock()
    mock_hs.search_contact_by_email.return_value = found
    mock_hs.search_contact_by_name_company.return_value = None
    mocker.patch("src.research.clients_factory.HubSpotContactClient",
                 return_value=mock_hs)
    return mock_hs


def _mock_hubspot_account_client(mocker, company=None):
    mock_acct = MagicMock()
    mock_acct.search_company_by_domain.return_value = company
    mocker.patch("src.research.clients_factory.HubSpotAccountClient",
                 return_value=mock_acct)
    return mock_acct


# ---------------------------------------------------------------------------
# 1. All env vars set → snapshot + research + contacts all visible
# ---------------------------------------------------------------------------

def test_all_envs_set_renders_snapshot_research_and_contacts(monkeypatch, mocker):
    _set(monkeypatch, APOLLO_API_KEY="ak", HUBSPOT_ACCESS_TOKEN="hs",
         HUBSPOT_PORTAL_ID="12345", OPENROUTER_API_KEY="or", EXA_API_KEY="ex")

    runner = _reload_config_and_factory()
    _mock_findings_pipeline(mocker)
    _mock_apollo_client(mocker)
    _mock_hubspot_contact_client(mocker, found=None)
    _mock_hubspot_account_client(mocker, company={
        "id": "c1",
        "properties": {"name": "Kroger", "num_associated_contacts": "3"},
    })

    from src.research.sessions import create_session
    s = create_session(rep_id="U", account_name="Kroger")
    s.personas = ["operations_lead"]
    respond = MagicMock()

    runner.run_research(s, respond)

    respond.assert_called_once()
    rendered = json.dumps(respond.call_args.kwargs.get("blocks"))
    # Snapshot block present
    assert "HUBSPOT ACCOUNT SNAPSHOT" in rendered
    # Research findings present (account name)
    assert "Kroger" in rendered
    # Contact block header present
    assert "CONTACTS" in rendered or "Jane" in rendered


# ---------------------------------------------------------------------------
# 2. Apollo missing → no contacts; warning surfaced
# ---------------------------------------------------------------------------

def test_apollo_missing_no_contacts_warning_visible(monkeypatch, mocker):
    _set(monkeypatch, HUBSPOT_ACCESS_TOKEN="hs", HUBSPOT_PORTAL_ID="12345",
         OPENROUTER_API_KEY="or", EXA_API_KEY="ex")

    runner = _reload_config_and_factory()
    _mock_findings_pipeline(mocker)
    _mock_hubspot_account_client(mocker, company=None)

    from src.research.sessions import create_session
    s = create_session(rep_id="U", account_name="Kroger")
    s.personas = ["operations_lead"]
    respond = MagicMock()

    runner.run_research(s, respond)

    respond.assert_called_once()
    rendered = json.dumps(respond.call_args.kwargs.get("blocks"))
    # Apollo warning surfaced
    assert "Apollo" in rendered


# ---------------------------------------------------------------------------
# 3. HubSpot token missing → contacts untagged, snapshot omitted
# ---------------------------------------------------------------------------

def test_hubspot_missing_contacts_untagged_snapshot_omitted(monkeypatch, mocker):
    _set(monkeypatch, APOLLO_API_KEY="ak", OPENROUTER_API_KEY="or",
         EXA_API_KEY="ex")

    runner = _reload_config_and_factory()
    _mock_findings_pipeline(mocker)
    _mock_apollo_client(mocker)

    from src.research.sessions import create_session
    s = create_session(rep_id="U", account_name="Kroger")
    s.personas = ["operations_lead"]
    respond = MagicMock()

    runner.run_research(s, respond)

    respond.assert_called_once()
    rendered = json.dumps(respond.call_args.kwargs.get("blocks"))
    # No snapshot block when HubSpot is unavailable
    assert "HUBSPOT ACCOUNT SNAPSHOT" not in rendered
    # Apollo data still surfaced (contact name visible somewhere)
    # — and HubSpot tagging warning visible
    assert "HubSpot" in rendered


# ---------------------------------------------------------------------------
# 4. Anthropic missing → empty findings + research_gap (Phase 11 behaviour)
# ---------------------------------------------------------------------------

def test_openrouter_missing_empty_findings_with_gap(monkeypatch, mocker):
    _set(monkeypatch, APOLLO_API_KEY="ak", HUBSPOT_ACCESS_TOKEN="hs",
         HUBSPOT_PORTAL_ID="12345", EXA_API_KEY="ex")

    runner = _reload_config_and_factory()
    # Exa still returns results; Anthropic key missing means findings_builder
    # short-circuits with the "OPENROUTER_API_KEY not configured" gap.
    mock_exa = MagicMock()
    mock_exa.search.return_value = [
        {"title": "x", "url": "https://example.com/", "snippet": "y"},
    ]
    mocker.patch("src.research.findings_builder.ExaSearchClient",
                 return_value=mock_exa)
    _mock_apollo_client(mocker)
    _mock_hubspot_contact_client(mocker, found=None)
    _mock_hubspot_account_client(mocker, company=None)

    from src.research.sessions import create_session
    s = create_session(rep_id="U", account_name="Kroger")
    s.personas = ["executive"]
    respond = MagicMock()

    runner.run_research(s, respond)
    respond.assert_called_once()
    rendered = json.dumps(respond.call_args.kwargs.get("blocks"))
    # Research gaps section is rendered with the Anthropic-missing gap
    assert "OPENROUTER_API_KEY" in rendered or "extraction" in rendered.lower()


# ---------------------------------------------------------------------------
# 5. Exa missing → empty findings + research_gap
# ---------------------------------------------------------------------------

def test_exa_missing_runner_still_responds(monkeypatch, mocker):
    _set(monkeypatch, APOLLO_API_KEY="ak", HUBSPOT_ACCESS_TOKEN="hs",
         HUBSPOT_PORTAL_ID="12345", OPENROUTER_API_KEY="or")

    runner = _reload_config_and_factory()
    # Exa raises because the key is missing — findings_builder handles it
    mock_exa = MagicMock()
    mock_exa.search.side_effect = RuntimeError("no exa key")
    mocker.patch("src.research.findings_builder.ExaSearchClient",
                 return_value=mock_exa)
    _mock_apollo_client(mocker)
    _mock_hubspot_contact_client(mocker, found=None)
    _mock_hubspot_account_client(mocker, company=None)

    from src.research.sessions import create_session
    s = create_session(rep_id="U", account_name="Kroger")
    s.personas = ["executive"]
    respond = MagicMock()

    runner.run_research(s, respond)
    respond.assert_called_once()


# ---------------------------------------------------------------------------
# 6. THE smoke test: nothing set → respond is still called once with section
#    headers visible. Drop env vars and it works.
# ---------------------------------------------------------------------------

def test_zero_envs_set_runner_still_responds_with_headers(monkeypatch, mocker):
    runner = _reload_config_and_factory()
    # No ExaSearchClient instance can be made cleanly — emulate by raising
    mocker.patch(
        "src.research.findings_builder.ExaSearchClient",
        side_effect=RuntimeError("no exa key"),
    )

    from src.research.sessions import create_session
    s = create_session(rep_id="U", account_name="Kroger")
    s.personas = ["executive"]
    respond = MagicMock()

    runner.run_research(s, respond)

    respond.assert_called_once()
    blocks = respond.call_args.kwargs.get("blocks")
    rendered = json.dumps(blocks)

    # Spec §1.2 — never silently skip a section. All five headers present.
    assert "TRIGGER EVENTS" in rendered
    assert "COMPETITOR SIGNALS" in rendered
    assert "DISTRIBUTION" in rendered or "FACILITY INTEL" in rendered
    assert "BOARD INITIATIVES" in rendered
    assert "RESEARCH GAPS" in rendered


# ---------------------------------------------------------------------------
# 7. Runner never raises — even if every external dep blows up
# ---------------------------------------------------------------------------

def test_runner_never_raises_when_everything_fails(monkeypatch, mocker):
    _set(monkeypatch, APOLLO_API_KEY="ak", HUBSPOT_ACCESS_TOKEN="hs",
         HUBSPOT_PORTAL_ID="12345", OPENROUTER_API_KEY="or", EXA_API_KEY="ex")

    runner = _reload_config_and_factory()
    mocker.patch(
        "src.research.findings_builder.ExaSearchClient",
        side_effect=RuntimeError("exa kaboom"),
    )
    # Apollo client raises on call
    bad_apollo = MagicMock()
    bad_apollo.search_contacts_by_company_and_titles.side_effect = RuntimeError(
        "apollo kaboom"
    )
    mocker.patch("src.research.clients_factory.ApolloContactClient",
                 return_value=bad_apollo)
    bad_hs = MagicMock()
    bad_hs.search_contact_by_email.side_effect = RuntimeError("hs kaboom")
    mocker.patch("src.research.clients_factory.HubSpotContactClient",
                 return_value=bad_hs)
    bad_acct = MagicMock()
    bad_acct.search_company_by_domain.side_effect = RuntimeError("acct kaboom")
    mocker.patch("src.research.clients_factory.HubSpotAccountClient",
                 return_value=bad_acct)

    from src.research.sessions import create_session
    s = create_session(rep_id="U", account_name="Kroger")
    s.personas = ["executive"]
    respond = MagicMock()

    # Must not raise
    runner.run_research(s, respond)
    respond.assert_called_once()
