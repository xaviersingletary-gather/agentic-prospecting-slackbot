"""Spec §1.4 — DC count without a source URL is BLOCKED, not just flagged.

A line containing a number plus 'distribution center' or 'DC' must not
appear in output unless it carries a source URL or `Source:` annotation.
"""


def test_dc_count_with_url_is_not_blocked():
    from src.utils.citation_validator import is_unsourced_dc_count

    assert not is_unsourced_dc_count(
        "• Operates 42 distribution centers — https://example.com/dcs"
    )


def test_dc_count_with_source_keyword_is_not_blocked():
    from src.utils.citation_validator import is_unsourced_dc_count

    assert not is_unsourced_dc_count(
        "• Operates 42 distribution centers [Source: https://example.com/dcs]"
    )


def test_unsourced_distribution_centers_phrase_is_blocked():
    from src.utils.citation_validator import is_unsourced_dc_count

    assert is_unsourced_dc_count("• Operates 42 distribution centers")


def test_unsourced_dc_acronym_is_blocked():
    from src.utils.citation_validator import is_unsourced_dc_count

    assert is_unsourced_dc_count("• Runs 35 DCs across the country")


def test_no_number_no_block():
    from src.utils.citation_validator import is_unsourced_dc_count

    # No specific count — not the DC-count attack surface
    assert not is_unsourced_dc_count(
        "• Operates a network of distribution centers"
    )


def test_output_formatter_drops_unsourced_dc_intel_items():
    from src.research.output_formatter import format_research_output

    findings = {
        "account_name": "Kroger",
        "trigger_events": [],
        "competitor_signals": [],
        "dc_intel": [
            {"claim": "Operates 35 DCs", "source_url": ""},
        ],
        "board_initiatives": [],
        "research_gaps": [],
    }
    out = format_research_output(findings)
    # Unsourced DC count must not appear at all
    assert "35 DCs" not in out
    # The section must still be present with the spec-mandated fallback
    assert "Could not confirm DC count from public sources" in out


def test_output_formatter_keeps_sourced_dc_intel_items():
    from src.research.output_formatter import format_research_output

    findings = {
        "account_name": "Kroger",
        "trigger_events": [],
        "competitor_signals": [],
        "dc_intel": [
            {"claim": "Operates 35 DCs", "source_url": "https://example.com/d"},
        ],
        "board_initiatives": [],
        "research_gaps": [],
    }
    out = format_research_output(findings)
    assert "35 DCs" in out
    assert "https://example.com/d" in out


def test_output_formatter_drops_unsourced_dc_keeps_sourced_dc():
    from src.research.output_formatter import format_research_output

    findings = {
        "account_name": "Kroger",
        "trigger_events": [],
        "competitor_signals": [],
        "dc_intel": [
            {"claim": "Operates 35 DCs", "source_url": ""},
            {"claim": "Opened new DC in Aurora", "source_url": "https://example.com/aurora"},
        ],
        "board_initiatives": [],
        "research_gaps": [],
    }
    out = format_research_output(findings)
    assert "35 DCs" not in out
    assert "Aurora" in out


def test_unsourced_non_dc_fact_gets_unverified_prefix_not_dropped():
    from src.research.output_formatter import format_research_output

    findings = {
        "account_name": "Kroger",
        "trigger_events": [
            {"claim": "Hired a new CSCO last quarter", "source_url": ""},
        ],
        "competitor_signals": [],
        "dc_intel": [],
        "board_initiatives": [],
        "research_gaps": [],
    }
    out = format_research_output(findings)
    assert "Hired a new CSCO last quarter" in out
    assert "⚠️ [Unverified]" in out
