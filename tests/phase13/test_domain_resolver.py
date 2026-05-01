"""Phase 13 — domain_resolver.

Resolve a company root domain from contact emails when possible, falling
back to a name-based heuristic. Just good enough to feed
`get_account_snapshot`.
"""


def test_picks_corporate_domain_from_contact_email():
    from src.research.domain_resolver import resolve_domain

    contacts = [
        {"first_name": "Jane", "last_name": "Doe", "email": "jane@kroger.com"},
    ]
    assert resolve_domain("Kroger", contacts) == "kroger.com"


def test_ignores_personal_email_domains_and_falls_back_to_corporate():
    from src.research.domain_resolver import resolve_domain

    contacts = [
        {"first_name": "Jane", "last_name": "Doe", "email": "jane@gmail.com"},
        {"first_name": "Joe", "last_name": "Smith", "email": "joe@yahoo.com"},
        {"first_name": "Sue", "last_name": "Lee", "email": "sue@kroger.com"},
    ]
    assert resolve_domain("Kroger", contacts) == "kroger.com"


def test_strips_subdomains_to_root():
    from src.research.domain_resolver import resolve_domain

    contacts = [
        {"first_name": "Jane", "last_name": "Doe", "email": "jane@hr.kroger.com"},
    ]
    assert resolve_domain("Kroger", contacts) == "kroger.com"


def test_falls_back_to_name_dot_com_when_no_corporate_email():
    from src.research.domain_resolver import resolve_domain

    contacts = [
        {"first_name": "Jane", "last_name": "Doe", "email": "jane@gmail.com"},
        {"first_name": "Joe", "last_name": "Smith", "email": ""},
    ]
    assert resolve_domain("Kroger", contacts) == "kroger.com"


def test_falls_back_when_contacts_empty():
    from src.research.domain_resolver import resolve_domain

    assert resolve_domain("Kroger", []) == "kroger.com"


def test_fallback_lowercases_and_strips_punctuation():
    from src.research.domain_resolver import resolve_domain

    assert resolve_domain("AbbVie Inc.", []) == "abbvieinc.com"
    assert resolve_domain("Procter & Gamble", []) == "proctergamble.com"


def test_fallback_strips_spaces():
    from src.research.domain_resolver import resolve_domain

    assert resolve_domain("Home Depot", []) == "homedepot.com"


def test_fallback_strips_non_ascii_unicode():
    from src.research.domain_resolver import resolve_domain

    # Smart quotes / accented chars get dropped; whatever ASCII alphanumerics
    # remain become the domain root.
    result = resolve_domain("Nestlé", [])
    # "nestl" is acceptable; "nestl.com" is the expected output
    assert result.endswith(".com")
    assert "é" not in result
    assert " " not in result


def test_ignores_outlook_hotmail_aol_proton():
    from src.research.domain_resolver import resolve_domain

    contacts = [
        {"email": "jane@outlook.com"},
        {"email": "joe@hotmail.com"},
        {"email": "sue@aol.com"},
        {"email": "x@proton.me"},
        {"email": "y@protonmail.com"},
        {"email": "z@me.com"},
        {"email": "i@icloud.com"},
        {"email": "real@kroger.com"},
    ]
    assert resolve_domain("Kroger", contacts) == "kroger.com"


def test_handles_missing_email_keys_gracefully():
    from src.research.domain_resolver import resolve_domain

    contacts = [
        {"first_name": "Jane", "last_name": "Doe"},  # no email key
    ]
    # Falls through to name-based default
    assert resolve_domain("Kroger", contacts) == "kroger.com"
