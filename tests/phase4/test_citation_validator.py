"""Spec §1.4 — every factual bullet without a Source URL gets prefixed
with `⚠️ [Unverified]`. Sourced bullets pass through unchanged.
Non-assertion bullets (no number, no proper noun) pass through too.
"""


def test_unsourced_assertion_bullet_gets_unverified_prefix():
    from src.utils.citation_validator import flag_if_unverified

    line = "• Kroger hired a new CSCO last quarter"
    out = flag_if_unverified(line)
    assert "⚠️ [Unverified]" in out
    assert "Kroger hired a new CSCO last quarter" in out


def test_sourced_bullet_passes_through_unchanged():
    from src.utils.citation_validator import flag_if_unverified

    line = "• Kroger operates 42 distribution centers — https://example.com/kroger-dcs"
    assert flag_if_unverified(line) == line


def test_bullet_with_source_keyword_passes_through_unchanged():
    from src.utils.citation_validator import flag_if_unverified

    line = "• Hired new CSCO [Source: https://example.com]"
    assert flag_if_unverified(line) == line


def test_already_flagged_bullet_not_double_flagged():
    from src.utils.citation_validator import flag_if_unverified

    line = "• ⚠️ [Unverified] — Some claim"
    out = flag_if_unverified(line)
    assert out.count("⚠️ [Unverified]") == 1


def test_non_assertion_bullet_passes_through_unchanged():
    from src.utils.citation_validator import flag_if_unverified

    line = "• no public data found"
    assert flag_if_unverified(line) == line


def test_prefix_lands_after_bullet_marker():
    from src.utils.citation_validator import flag_if_unverified

    line = "• Kroger hired a new CSCO"
    out = flag_if_unverified(line)
    # Bullet marker preserved; prefix follows
    assert out.startswith("• ⚠️ [Unverified]")
