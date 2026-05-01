"""Phase 12 — Apollo legacy preservation.

Moving `src/integrations/apollo.py` → `src/integrations/apollo/legacy.py`
must not break existing callers. `src/agents/discovery.py` imports
`ApolloClient` and `PERSONA_TITLE_KEYWORDS` from the package root.
"""


def test_legacy_apollo_client_still_importable_from_package_root():
    from src.integrations.apollo import ApolloClient

    assert ApolloClient is not None
    # Must still be a class — not a module accidentally re-exported
    assert isinstance(ApolloClient, type)


def test_legacy_persona_title_keywords_still_importable_from_package_root():
    from src.integrations.apollo import PERSONA_TITLE_KEYWORDS

    # Legacy taxonomy used by src/agents/discovery.py
    assert isinstance(PERSONA_TITLE_KEYWORDS, dict)
    assert "TDM" in PERSONA_TITLE_KEYWORDS
    assert "ODM" in PERSONA_TITLE_KEYWORDS
    assert "FS" in PERSONA_TITLE_KEYWORDS
    assert "IT" in PERSONA_TITLE_KEYWORDS
    assert "Safety" in PERSONA_TITLE_KEYWORDS


def test_new_apollo_contact_client_importable_from_package_root():
    from src.integrations.apollo import ApolloContactClient

    assert ApolloContactClient is not None
    assert isinstance(ApolloContactClient, type)


def test_legacy_apollo_client_importable_from_legacy_submodule():
    """Direct import from the legacy module also works — covers anyone who
    explicitly references the legacy path."""
    from src.integrations.apollo.legacy import ApolloClient

    assert ApolloClient is not None


def test_discovery_agent_still_imports_apollo_legacy():
    """Smoke test: the existing consumer (src/agents/discovery.py) must
    still import its dependencies after the package move."""
    # Pure import smoke — if the move broke discovery, this fails.
    from src.agents import discovery  # noqa: F401
