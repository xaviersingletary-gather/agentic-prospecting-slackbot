"""Phase 18 — Provenance labeling.

Every finding carries a `provenance` ∈ {sourced, inferred, not_found}.
The sanitizer in `findings_builder._normalize_finding` enforces the
per-provenance field contract; `output_formatter._render_fact_*` renders
distinct labels (✅ / 🔍 / ❌) so reps know what to trust verbatim,
what to hedge, and what to ask in discovery.
"""
import json
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Sanitizer contract
# ---------------------------------------------------------------------------


def test_sourced_finding_round_trips():
    from src.research.findings_builder import _normalize_finding

    out = _normalize_finding(
        {
            "provenance": "sourced",
            "claim": "Operates 47 DCs in North America",
            "source_url": "https://volvogroup.com/network",
            "source_date": "2026-03",
        },
        "dc_intel",
    )
    assert out == {
        "provenance": "sourced",
        "claim": "Operates 47 DCs in North America",
        "source_url": "https://volvogroup.com/network",
        "source_date": "2026-03",
    }


def test_inferred_finding_requires_basis():
    from src.research.findings_builder import _normalize_finding

    # Missing inference_basis -> dropped
    assert _normalize_finding(
        {
            "provenance": "inferred",
            "claim": "Estimated 35–45 DCs",
        },
        "dc_intel",
    ) is None

    # With basis -> kept (no URL required)
    out = _normalize_finding(
        {
            "provenance": "inferred",
            "claim": "Estimated 35–45 DCs",
            "inference_basis": "$4.2B revenue + grocery industry baseline",
        },
        "dc_intel",
    )
    assert out["provenance"] == "inferred"
    assert out["claim"] == "Estimated 35–45 DCs"
    assert "grocery" in out["inference_basis"]
    assert "source_url" not in out


def test_not_found_finding_requires_question():
    from src.research.findings_builder import _normalize_finding

    # No discovery_question -> dropped (a not_found without a question
    # buys nothing for the rep)
    assert _normalize_finding(
        {
            "provenance": "not_found",
            "label": "WMS of record",
        },
        "competitor_signals",
    ) is None

    out = _normalize_finding(
        {
            "provenance": "not_found",
            "label": "WMS of record",
            "discovery_question": "What WMS are you running today?",
        },
        "competitor_signals",
    )
    assert out == {
        "provenance": "not_found",
        "label": "WMS of record",
        "discovery_question": "What WMS are you running today?",
    }


def test_legacy_finding_without_provenance_defaults_to_sourced():
    """Snapshots written before the contract change have no `provenance`
    key. They should still render — never silently disappear."""
    from src.research.findings_builder import _normalize_finding

    out = _normalize_finding(
        {
            "claim": "Symbotic deployed at 5 facilities",
            "source_url": "https://example.com/sym",
        },
        "competitor_signals",
    )
    assert out is not None
    assert out["provenance"] == "sourced"
    assert out["source_url"] == "https://example.com/sym"


def test_legacy_finding_without_url_is_dropped():
    """An older claim with no URL and no inference basis has no safe path
    forward — drop it rather than promote to an unverified rendering."""
    from src.research.findings_builder import _normalize_finding

    assert _normalize_finding(
        {"claim": "Some unverified guess"}, "trigger_events"
    ) is None


def test_unknown_provenance_recovers_via_back_compat():
    """If the model emits a bogus provenance value but the rest of the
    shape is valid, recover into the closest legal provenance rather
    than silently dropping a usable finding."""
    from src.research.findings_builder import _normalize_finding

    out = _normalize_finding(
        {
            "provenance": "rumor",
            "claim": "x",
            "source_url": "https://example.com",
        },
        "trigger_events",
    )
    assert out is not None
    assert out["provenance"] == "sourced"


def test_unknown_provenance_with_no_recoverable_shape_is_dropped():
    """No URL, no inference basis, no discovery question → nothing to
    recover into; drop rather than render an unverified line."""
    from src.research.findings_builder import _normalize_finding

    assert _normalize_finding(
        {"provenance": "rumor", "claim": "x"},
        "trigger_events",
    ) is None


# ---------------------------------------------------------------------------
# Formatter rendering — mrkdwn text path
# ---------------------------------------------------------------------------


def _findings_with(provenance_item):
    return {
        "account_name": "Volvo Group",
        "trigger_events": [provenance_item],
        "competitor_signals": [],
        "dc_intel": [],
        "board_initiatives": [],
        "research_gaps": [],
    }


def test_sourced_renders_check_label_and_url():
    from src.research.output_formatter import format_research_output, PROV_SOURCED

    out = format_research_output(_findings_with({
        "provenance": "sourced",
        "claim": "CSCO referenced inventory accuracy on Q1 call",
        "source_url": "https://example.com/q1",
        "source_date": "2026-04",
    }))
    assert PROV_SOURCED in out
    assert "CSCO referenced inventory accuracy" in out
    assert "example.com" in out
    assert "2026-04" in out


def test_inferred_renders_magnifier_label_and_basis():
    from src.research.output_formatter import format_research_output, PROV_INFERRED

    out = format_research_output(_findings_with({
        "provenance": "inferred",
        "claim": "Likely running Manhattan or Blue Yonder WMS",
        "inference_basis": "Tier-1 grocer scale + automation vendor signals",
    }))
    assert PROV_INFERRED in out
    assert "basis:" in out
    assert "Tier-1 grocer" in out


def test_not_found_renders_x_label_and_discovery_question():
    from src.research.output_formatter import format_research_output, PROV_NOT_FOUND

    out = format_research_output(_findings_with({
        "provenance": "not_found",
        "label": "WMS of record",
        "discovery_question": "What WMS are you running today?",
    }))
    assert PROV_NOT_FOUND in out
    assert "WMS of record" in out
    assert "ask:" in out
    assert "What WMS are you running today?" in out


def test_three_provenance_labels_visually_distinct():
    """The three labels must each be unique strings so a rep skimming
    can tell them apart at a glance."""
    from src.research.output_formatter import (
        PROV_SOURCED,
        PROV_INFERRED,
        PROV_NOT_FOUND,
    )
    labels = {PROV_SOURCED, PROV_INFERRED, PROV_NOT_FOUND}
    assert len(labels) == 3


def test_legacy_findings_still_render_with_check_label():
    """Snapshots from before the provenance contract should render
    unchanged from the rep's perspective — including the ✅ Sourced
    label, since they have URLs."""
    from src.research.output_formatter import format_research_output, PROV_SOURCED

    out = format_research_output({
        "account_name": "Kroger",
        "trigger_events": [
            {"claim": "Opened new DC", "source_url": "https://example.com/dc"}
        ],
        "competitor_signals": [],
        "dc_intel": [],
        "board_initiatives": [],
        "research_gaps": [],
    })
    assert PROV_SOURCED in out
    assert "Opened new DC" in out


# ---------------------------------------------------------------------------
# Formatter rendering — Block Kit path
# ---------------------------------------------------------------------------


def _flatten_blocks(blocks):
    """Pull all mrkdwn text out of a Block Kit list for substring assertions."""
    out = []
    for b in blocks:
        if b.get("type") == "section":
            out.append(b.get("text", {}).get("text", ""))
        elif b.get("type") == "context":
            for el in b.get("elements", []):
                out.append(el.get("text", ""))
        elif b.get("type") == "header":
            out.append(b.get("text", {}).get("text", ""))
    return "\n".join(out)


def test_block_kit_sourced_emits_section_and_context():
    from src.research.output_formatter import build_research_blocks, PROV_SOURCED

    blocks = build_research_blocks(_findings_with({
        "provenance": "sourced",
        "claim": "Hiring 2 automation engineers in Memphis",
        "source_url": "https://example.com/jobs",
        "source_date": "2026-05",
    }))
    text = _flatten_blocks(blocks)
    assert PROV_SOURCED in text
    assert "Hiring 2 automation engineers" in text
    assert "example.com" in text
    assert "2026-05" in text


def test_block_kit_inferred_emits_basis_context():
    from src.research.output_formatter import build_research_blocks, PROV_INFERRED

    blocks = build_research_blocks(_findings_with({
        "provenance": "inferred",
        "claim": "~40 DCs estimated",
        "inference_basis": "Revenue + industry pallet throughput",
    }))
    text = _flatten_blocks(blocks)
    assert PROV_INFERRED in text
    assert "basis:" in text
    assert "Revenue + industry pallet throughput" in text


def test_block_kit_not_found_emits_discovery_question_context():
    from src.research.output_formatter import build_research_blocks, PROV_NOT_FOUND

    blocks = build_research_blocks(_findings_with({
        "provenance": "not_found",
        "label": "Named automation deployment",
        "discovery_question": "Have you piloted any robotics on the floor?",
    }))
    text = _flatten_blocks(blocks)
    assert PROV_NOT_FOUND in text
    assert "Named automation deployment" in text
    assert "ask:" in text
    assert "Have you piloted any robotics" in text


# ---------------------------------------------------------------------------
# End-to-end: LLM emits provenance, sanitizer + formatter carry it through
# ---------------------------------------------------------------------------


def _llm_response(text):
    choice = MagicMock()
    choice.message = MagicMock()
    choice.message.content = text
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def test_e2e_provenance_flows_through_pipeline(mocker):
    """If the LLM emits a mix of sourced/inferred/not_found findings, the
    Slack output should render all three labels — proves the contract
    flows end-to-end."""
    from src.research.findings_builder import build_findings
    from src.research.sessions import ResearchSession
    from src.research.output_formatter import (
        format_research_output,
        PROV_SOURCED,
        PROV_INFERRED,
        PROV_NOT_FOUND,
    )

    mock_exa = MagicMock()
    mock_exa.search.return_value = [
        {"title": "t", "url": "https://example.com/x", "snippet": "s"}
    ]
    mocker.patch(
        "src.research.findings_builder.ExaSearchClient",
        return_value=mock_exa,
    )

    mock_llm = MagicMock()
    mock_llm.chat.completions.create.return_value = _llm_response(json.dumps({
        "trigger_events": [
            {
                "provenance": "sourced",
                "claim": "CSCO flagged inventory accuracy on Q1 earnings",
                "source_url": "https://example.com/q1",
                "source_date": "2026-04",
            }
        ],
        "competitor_signals": [
            {
                "provenance": "inferred",
                "claim": "Likely evaluating WMS upgrade in 2026",
                "inference_basis": "Hiring posts mention WMS migration experience",
            }
        ],
        "dc_intel": [
            {
                "provenance": "not_found",
                "label": "Exact DC count",
                "discovery_question": "How many facilities do you operate in NA?",
            }
        ],
        "board_initiatives": [],
        "research_gaps": [],
    }))
    mocker.patch(
        "src.research.findings_builder.OpenAI",
        return_value=mock_llm,
    )
    mocker.patch(
        "src.research.findings_builder.settings.OPENROUTER_API_KEY",
        "test-key",
    )

    s = ResearchSession(
        session_id="s1", rep_id="U1", account_name="Volvo Group",
        personas=["executive"],
    )
    findings = build_findings(s)

    assert findings["trigger_events"][0]["provenance"] == "sourced"
    assert findings["competitor_signals"][0]["provenance"] == "inferred"
    assert findings["dc_intel"][0]["provenance"] == "not_found"

    out = format_research_output(findings)
    assert PROV_SOURCED in out
    assert PROV_INFERRED in out
    assert PROV_NOT_FOUND in out
    assert "How many facilities do you operate" in out


def test_dc_intel_unsourced_count_still_blocked():
    """Even with provenance labeling, a numeric DC count claim without a
    sourced URL must not appear. The S1.4 gate stays intact — model
    should emit it as `not_found` if it doesn't have a source."""
    from src.research.findings_builder import _normalize_finding

    # An inferred DC count is allowed by `_normalize_finding` itself
    # (provenance is honored), but the formatter drops it in the DC
    # section. Verified by `test_dc_count_blocking.py` for sourced-but-
    # numeric claims and reinforced here for inferences.
    from src.research.output_formatter import _render_fact_bullet

    out = _render_fact_bullet(
        {
            "provenance": "inferred",
            "claim": "47 distribution centers",
            "inference_basis": "Estimated from revenue",
        },
        is_dc=True,
    )
    assert out is None, "Inferred DC count must be dropped from dc_intel"
