"""Phase 11 — Findings builder.

Orchestrator: account name + selected personas → findings dict in the
v1 schema. Calls Exa for snippets, hands them to the LLM (OpenRouter,
OpenAI-compatible) with a system prompt that requires `[Source: URL]`
on every claim, and parses the JSON output back into the schema.

External calls (Exa, OpenRouter) are mocked.
"""
import json
from unittest.mock import MagicMock


def _llm_response(text: str):
    """Build a minimal openai.chat.completions.create() return value."""
    choice = MagicMock()
    choice.message = MagicMock()
    choice.message.content = text
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _good_claude_json():
    return json.dumps({
        "trigger_events": [
            {
                "claim": "Kroger announced a new fulfillment center in Ohio.",
                "source_url": "https://example.com/news/kroger-ohio",
            }
        ],
        "competitor_signals": [
            {
                "claim": "Symbotic deployment referenced in Q4 earnings.",
                "source_url": "https://example.com/earnings/q4",
            }
        ],
        "dc_intel": [
            {
                "claim": "Operates 35 distribution centers across the U.S.",
                "source_url": "https://example.com/sec/10k",
            }
        ],
        "board_initiatives": [
            {
                "claim": "Cost reduction priority called out by CFO.",
                "source_url": "https://example.com/investor-day",
            }
        ],
        "research_gaps": [
            "WMS migration status not confirmed in public filings.",
        ],
    })


def _patch_exa(mocker, results=None):
    if results is None:
        results = [
            {
                "title": "Kroger expansion",
                "url": "https://example.com/news/kroger-ohio",
                "snippet": "New fulfillment center in Ohio announced.",
                "publishedDate": "2026-02-01",
            },
            {
                "title": "Kroger Q4 earnings",
                "url": "https://example.com/earnings/q4",
                "snippet": "Mentioned Symbotic robotics in DC operations.",
                "publishedDate": "2026-02-15",
            },
        ]
    mock_client = MagicMock()
    mock_client.search.return_value = results
    mocker.patch(
        "src.research.findings_builder.ExaSearchClient",
        return_value=mock_client,
    )
    return mock_client


def _patch_openrouter(mocker, text):
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _llm_response(text)
    mocker.patch(
        "src.research.findings_builder.OpenAI",
        return_value=mock_client,
    )
    # Settings may not have OPENROUTER_API_KEY at test time; force it on
    # so the builder doesn't bail out before calling the LLM.
    mocker.patch(
        "src.research.findings_builder.settings.OPENROUTER_API_KEY",
        "test-openrouter-key",
    )
    return mock_client


def test_findings_dict_matches_v1_schema(mocker):
    from src.research.findings_builder import build_findings
    from src.research.sessions import ResearchSession

    _patch_exa(mocker)
    _patch_openrouter(mocker, _good_claude_json())

    s = ResearchSession(
        session_id="s1", rep_id="U1", account_name="Kroger",
        personas=["operations_lead"],
    )
    findings = build_findings(s)

    assert findings["account_name"] == "Kroger"
    for key in (
        "trigger_events",
        "competitor_signals",
        "dc_intel",
        "board_initiatives",
        "research_gaps",
    ):
        assert key in findings
    # Every fact carries a source URL
    for key in ("trigger_events", "competitor_signals", "dc_intel", "board_initiatives"):
        for item in findings[key]:
            assert item.get("source_url"), f"{key} item missing source_url: {item}"
            assert item.get("claim")


def test_account_name_flows_to_exa_queries(mocker):
    from src.research.findings_builder import build_findings
    from src.research.sessions import ResearchSession

    exa = _patch_exa(mocker)
    _patch_openrouter(mocker, _good_claude_json())

    s = ResearchSession(
        session_id="s1", rep_id="U1", account_name="Sysco Foods",
        personas=["executive"],
    )
    build_findings(s)

    # At least one Exa query should mention the account name
    queries = [call.args[0] if call.args else call.kwargs.get("query", "")
               for call in exa.search.call_args_list]
    assert any("Sysco Foods" in q for q in queries)


def test_personas_influence_anthropic_prompt(mocker):
    from src.research.findings_builder import build_findings
    from src.research.sessions import ResearchSession

    _patch_exa(mocker)
    llm_client = _patch_openrouter(mocker, _good_claude_json())

    s = ResearchSession(
        session_id="s1", rep_id="U1", account_name="Kroger",
        personas=["operations_lead", "executive"],
    )
    build_findings(s)

    # Inspect the chat.completions.create call — persona labels should be in the user message
    create_kwargs = llm_client.chat.completions.create.call_args.kwargs
    messages = create_kwargs.get("messages", [])
    user_text = " ".join(
        m.get("content", "") if isinstance(m.get("content"), str)
        else json.dumps(m.get("content", ""))
        for m in messages
        if m.get("role") == "user"
    ).lower()
    assert "warehouse" in user_text or "operations_lead" in user_text
    assert "executive" in user_text or "supply chain" in user_text


def test_empty_exa_results_produce_empty_sections_with_research_gap(mocker):
    from src.research.findings_builder import build_findings
    from src.research.sessions import ResearchSession

    _patch_exa(mocker, results=[])
    # Even if Anthropic is called, returns nothing
    _patch_openrouter(mocker, json.dumps({
        "trigger_events": [],
        "competitor_signals": [],
        "dc_intel": [],
        "board_initiatives": [],
        "research_gaps": [],
    }))

    s = ResearchSession(
        session_id="s1", rep_id="U1", account_name="ObscureCo",
        personas=["executive"],
    )
    findings = build_findings(s)

    assert findings["account_name"] == "ObscureCo"
    assert findings["trigger_events"] == []
    assert findings["competitor_signals"] == []
    assert findings["dc_intel"] == []
    assert findings["board_initiatives"] == []
    # A research_gap should explain that no sources were found
    gaps_text = " ".join(findings["research_gaps"]).lower()
    assert "no" in gaps_text or "exa" in gaps_text or "source" in gaps_text


def test_bad_claude_json_falls_back_gracefully(mocker):
    from src.research.findings_builder import build_findings
    from src.research.sessions import ResearchSession

    _patch_exa(mocker)
    _patch_openrouter(mocker, "this is not JSON {{{")

    s = ResearchSession(
        session_id="s1", rep_id="U1", account_name="Kroger",
        personas=["executive"],
    )
    findings = build_findings(s)

    # All sections empty; a research_gap mentions extraction failure
    for key in ("trigger_events", "competitor_signals", "dc_intel", "board_initiatives"):
        assert findings[key] == []
    gaps_text = " ".join(findings["research_gaps"]).lower()
    assert "extract" in gaps_text or "fail" in gaps_text or "parse" in gaps_text


def test_claude_json_in_fenced_code_block_is_parsed(mocker):
    from src.research.findings_builder import build_findings
    from src.research.sessions import ResearchSession

    _patch_exa(mocker)
    fenced = "Here you go:\n```json\n" + _good_claude_json() + "\n```\nDone."
    _patch_openrouter(mocker, fenced)

    s = ResearchSession(
        session_id="s1", rep_id="U1", account_name="Kroger",
        personas=["executive"],
    )
    findings = build_findings(s)
    assert len(findings["trigger_events"]) >= 1


# ---------------------------------------------------------------------------
# Parallel Exa search — wall-clock + behavior contract
# ---------------------------------------------------------------------------

def test_exa_searches_run_in_parallel(mocker):
    """The 4 Exa searches must execute concurrently. Sequential floor for
    a 0.4s-per-call mock is ~1.6s; parallel completes in ~0.4s. Cap at
    1.0s to leave headroom for thread-pool spin-up under CI load."""
    import time
    from src.research.findings_builder import _run_exa_searches

    def slow_search(query, num_results):
        time.sleep(0.4)
        return [{"title": "t", "url": "https://example.com", "snippet": "s"}]

    mock_client = MagicMock()
    mock_client.search.side_effect = slow_search
    mocker.patch(
        "src.research.findings_builder.ExaSearchClient",
        return_value=mock_client,
    )

    start = time.monotonic()
    snippets, all_failed = _run_exa_searches("Kroger")
    elapsed = time.monotonic() - start

    assert not all_failed
    assert len(snippets) == 4
    assert elapsed < 1.0, f"expected parallel execution; took {elapsed:.2f}s"


def test_partial_exa_failure_yields_empty_for_failed_topic(mocker):
    """One topic raising must NOT break the others. The failed topic gets
    [] in the return dict; `all_failed` stays False."""
    from src.research.findings_builder import _run_exa_searches

    def search(query, num_results):
        # Competitor query template contains "Symbotic" — fail only that one.
        if "Symbotic" in query:
            raise RuntimeError("boom")
        return [{"title": "t", "url": "https://example.com", "snippet": "s"}]

    mock_client = MagicMock()
    mock_client.search.side_effect = search
    mocker.patch(
        "src.research.findings_builder.ExaSearchClient",
        return_value=mock_client,
    )

    snippets, all_failed = _run_exa_searches("Kroger")
    assert not all_failed
    assert snippets["competitor_signals"] == []
    assert snippets["trigger_events"] != []
    assert snippets["dc_intel"] != []
    assert snippets["board_initiatives"] != []


def test_progress_emitted_for_each_topic(mocker):
    """Every topic label must surface in on_progress, regardless of
    completion order."""
    from src.research.findings_builder import _run_exa_searches

    mock_client = MagicMock()
    mock_client.search.return_value = []
    mocker.patch(
        "src.research.findings_builder.ExaSearchClient",
        return_value=mock_client,
    )

    seen: list[str] = []
    _run_exa_searches("Kroger", on_progress=lambda m: seen.append(m))

    joined = " ".join(seen).lower()
    for label in (
        "trigger events",
        "competitor signals",
        "distribution / facility intel",
        "board initiatives",
    ):
        assert label in joined, f"missing progress for {label!r}; saw: {seen}"
