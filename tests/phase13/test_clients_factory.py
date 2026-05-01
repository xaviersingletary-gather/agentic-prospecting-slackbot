"""Phase 13 — clients_factory.

Each factory reads env vars at call time and returns either an instance
(when the relevant env var is set) or None (when missing). The runner
inspects what it got and degrades gracefully.

Format validation is NOT done — an invalid token still yields a client;
the API call is what fails. This keeps the factory dumb and the failure
mode at the integration boundary.

We override settings directly (not env vars) because src/config.py uses
`load_dotenv()` to read a local `.env` at import time — making
monkeypatch.delenv ineffective for tests.
"""
import pytest


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch):
    # Override the settings singleton's relevant fields so the factory
    # is exercised in a known state regardless of the local .env.
    from src.config import settings
    monkeypatch.setattr(settings, "APOLLO_API_KEY", "", raising=False)
    monkeypatch.setattr(settings, "HUBSPOT_ACCESS_TOKEN", "", raising=False)
    monkeypatch.setattr(settings, "HUBSPOT_PORTAL_ID", "", raising=False)
    yield


def test_get_apollo_client_returns_none_when_env_unset():
    from src.research import clients_factory
    assert clients_factory.get_apollo_client() is None


def test_get_apollo_client_returns_instance_when_env_set(monkeypatch):
    from src.config import settings
    monkeypatch.setattr(settings, "APOLLO_API_KEY", "test-apollo-key")

    from src.research import clients_factory
    from src.integrations.apollo.client import ApolloContactClient

    client = clients_factory.get_apollo_client()
    assert client is not None
    assert isinstance(client, ApolloContactClient)


def test_get_hubspot_contact_client_returns_none_when_env_unset():
    from src.research import clients_factory
    assert clients_factory.get_hubspot_contact_client() is None


def test_get_hubspot_contact_client_returns_instance_when_env_set(monkeypatch):
    from src.config import settings
    monkeypatch.setattr(settings, "HUBSPOT_ACCESS_TOKEN", "pat-na1-test")

    from src.research import clients_factory
    from src.integrations.hubspot.client import HubSpotContactClient

    client = clients_factory.get_hubspot_contact_client()
    assert client is not None
    assert isinstance(client, HubSpotContactClient)


def test_get_hubspot_account_client_returns_none_when_env_unset():
    from src.research import clients_factory
    assert clients_factory.get_hubspot_account_client() is None


def test_get_hubspot_account_client_returns_instance_when_env_set(monkeypatch):
    from src.config import settings
    monkeypatch.setattr(settings, "HUBSPOT_ACCESS_TOKEN", "pat-na1-test")

    from src.research import clients_factory
    from src.integrations.hubspot.account_snapshot import HubSpotAccountClient

    client = clients_factory.get_hubspot_account_client()
    assert client is not None
    assert isinstance(client, HubSpotAccountClient)


def test_get_hubspot_portal_id_returns_none_when_env_unset():
    from src.research import clients_factory
    assert clients_factory.get_hubspot_portal_id() is None


def test_get_hubspot_portal_id_returns_value_when_env_set(monkeypatch):
    from src.config import settings
    monkeypatch.setattr(settings, "HUBSPOT_PORTAL_ID", "12345678")

    from src.research import clients_factory
    assert clients_factory.get_hubspot_portal_id() == "12345678"


def test_factory_does_not_validate_token_format(monkeypatch):
    """An obviously invalid token still produces a client — the factory's
    job is presence detection, not validation. The API call fails later."""
    from src.config import settings
    monkeypatch.setattr(settings, "APOLLO_API_KEY", "not-a-real-key-but-non-empty")
    monkeypatch.setattr(settings, "HUBSPOT_ACCESS_TOKEN", "garbage")

    from src.research import clients_factory
    assert clients_factory.get_apollo_client() is not None
    assert clients_factory.get_hubspot_contact_client() is not None
