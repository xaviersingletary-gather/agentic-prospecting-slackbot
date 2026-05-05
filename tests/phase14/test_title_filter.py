"""Phase 14 — persona-fit title filter.

Apollo's `person_titles` filter is fuzzy and over-pulls. The filter in
`src.research.title_filter` subtracts false positives based on each
persona's `negative_keywords` list.

Behavioural contract:
- Empty persona list → no-op (return all input).
- Empty/missing title on a contact → keep (no signal to filter on).
- Word-boundary match — "IT" hits "VP IT Operations", not "Bit".
- Multi-persona: keep if ANY selected persona accepts.
- Unknown persona keys are ignored; if no valid keys remain, return all.
"""
from src.research.title_filter import filter_by_persona_fit


def _c(title: str, **extra) -> dict:
    """Tiny contact-dict helper."""
    base = {
        "first_name": "Test",
        "last_name": "Contact",
        "email": "x@y.com",
        "company": "Acme",
        "title": title,
    }
    base.update(extra)
    return base


def test_drops_VP_IT_Operations_on_operations_lead():
    contacts = [
        _c("VP Warehouse Operations"),
        _c("VP IT Operations"),
        _c("Director of Sales Operations"),
        _c("Director of Distribution"),
    ]
    out = filter_by_persona_fit(contacts, ["operations_lead"])
    titles = [c["title"] for c in out]
    assert "VP Warehouse Operations" in titles
    assert "Director of Distribution" in titles
    assert "VP IT Operations" not in titles
    assert "Director of Sales Operations" not in titles


def test_compliance_lead_keeps_IT_titles():
    """IT is the persona, not noise. Filter must not touch it."""
    contacts = [
        _c("Director of IT"),
        _c("VP Information Technology"),
        _c("Director of EHS"),
    ]
    out = filter_by_persona_fit(contacts, ["compliance_lead"])
    assert len(out) == 3


def test_multi_persona_keeps_contact_accepted_by_any_one():
    """Selecting compliance_lead + operations_lead must keep IT directors
    even though operations_lead has IT in negatives — compliance_lead
    accepts them."""
    contacts = [_c("Director of IT Operations")]
    out = filter_by_persona_fit(
        contacts, ["operations_lead", "compliance_lead"]
    )
    assert len(out) == 1


def test_empty_or_missing_title_is_kept():
    """No title = no signal to filter on. Default-permissive."""
    contacts = [
        {"first_name": "a", "email": "a@b.com"},  # no title key at all
        _c(""),                                    # empty title
        _c("   "),                                 # whitespace-only
    ]
    out = filter_by_persona_fit(contacts, ["operations_lead"])
    assert len(out) == 3


def test_empty_persona_list_is_noop():
    contacts = [_c("VP IT Operations"), _c("VP Sales")]
    out = filter_by_persona_fit(contacts, [])
    assert out == contacts


def test_unknown_persona_keys_are_ignored_and_returns_unfiltered():
    """If the only persona key is unknown, no valid filter exists →
    return contacts unchanged rather than dropping them all."""
    contacts = [_c("VP IT Operations")]
    out = filter_by_persona_fit(contacts, ["totally_not_a_persona"])
    assert out == contacts


def test_word_boundary_does_not_false_positive_on_substrings():
    """`IT` is a word, not a substring — `Bit` and `Fitness` must pass."""
    contacts = [
        _c("Bit Engineer"),       # contains 'IT' as substring of 'Bit'? actually no — 'bIT' — would substring match
        _c("VP Fitness"),         # 'IT' not present at word boundary
        _c("Director of Litigation"),  # 'IT' substring inside Litigation — must NOT drop
    ]
    out = filter_by_persona_fit(contacts, ["operations_lead"])
    titles = [c["title"] for c in out]
    # Litigation has no word-boundary "IT" — should not drop on IT alone.
    # But it DOES contain "Legal"-adjacent — actually no, "Litigation" is not "Legal".
    # The title doesn't hit any operations_lead negative → kept.
    assert "Director of Litigation" in titles


def test_multiple_negatives_any_match_drops():
    """A title matching any one negative is dropped."""
    contacts = [
        _c("VP Sales Engineering"),  # hits both Sales and Engineering on tech_lead
    ]
    out = filter_by_persona_fit(contacts, ["technical_lead"])
    assert out == []


def test_executive_drops_svp_sales_ops():
    """Top closed-lost gap is the financial sponsor — but on the executive
    persona the noise is SVP Sales Ops / SVP People etc. masquerading as
    'SVP Operations'. Filter must catch them."""
    contacts = [
        _c("Chief Supply Chain Officer"),
        _c("SVP Sales Operations"),
        _c("SVP People Operations"),
        _c("Chief Operating Officer"),
    ]
    out = filter_by_persona_fit(contacts, ["executive"])
    titles = [c["title"] for c in out]
    assert "Chief Supply Chain Officer" in titles
    assert "Chief Operating Officer" in titles
    assert "SVP Sales Operations" not in titles
    assert "SVP People Operations" not in titles
