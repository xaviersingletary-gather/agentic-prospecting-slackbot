"""Phase 11 — runner integration.

`run_research(session, respond)` keeps its public signature. The
placeholder is replaced by a real builder, but the wiring (respond is
called once with replace_original=True, blocks list contains account
name) must not regress.

External calls (Exa, OpenRouter) are mocked so this test never hits the
network.
"""
import json
from unittest.mock import MagicMock


def _mock_exa_and_llm(mocker):
    exa_results = [
        {
            "title": "Kroger expands DC network",
            "url": "https://example.com/news",
            "snippet": "Kroger announced new DC.",
            "publishedDate": "2026-01-01",
        },
    ]
    mock_exa = MagicMock()
    mock_exa.search.return_value = exa_results
    mocker.patch(
        "src.research.findings_builder.ExaSearchClient",
        return_value=mock_exa,
    )

    payload = {
        "trigger_events": [
            {"claim": "Kroger expands DC network",
             "source_url": "https://example.com/news"},
        ],
        "competitor_signals": [],
        "dc_intel": [],
        "board_initiatives": [],
        "research_gaps": [],
    }
    choice = MagicMock()
    choice.message = MagicMock()
    choice.message.content = json.dumps(payload)
    resp = MagicMock()
    resp.choices = [choice]
    mock_llm = MagicMock()
    mock_llm.chat.completions.create.return_value = resp
    mocker.patch(
        "src.research.findings_builder.OpenAI",
        return_value=mock_llm,
    )
    mocker.patch(
        "src.research.findings_builder.settings.OPENROUTER_API_KEY",
        "test-openrouter-key",
    )


def test_run_research_still_posts_blocks(mocker):
    from src.research.runner import run_research
    from src.research.sessions import create_session

    _mock_exa_and_llm(mocker)

    s = create_session(rep_id="U1", account_name="Kroger")
    s.personas = ["csco"]
    respond = MagicMock()

    run_research(s, respond)

    respond.assert_called_once()
    kwargs = respond.call_args.kwargs
    assert kwargs.get("response_type") == "ephemeral"
    assert kwargs.get("replace_original") is True
    blocks = kwargs.get("blocks")
    assert isinstance(blocks, list) and blocks


def test_run_research_account_name_in_blocks(mocker):
    from src.research.runner import run_research
    from src.research.sessions import create_session

    _mock_exa_and_llm(mocker)

    s = create_session(rep_id="U1", account_name="Kroger")
    s.personas = ["csco"]
    respond = MagicMock()

    run_research(s, respond)

    rendered = json.dumps(respond.call_args.kwargs.get("blocks"))
    assert "Kroger" in rendered


def test_run_research_does_not_raise_when_llm_fails(mocker):
    """Spec hard constraint: never raise out of the runner. Slack should
    still see a (mostly empty) research dump rather than a crash."""
    from src.research.runner import run_research
    from src.research.sessions import create_session

    # Exa returns OK results, LLM raises
    mock_exa = MagicMock()
    mock_exa.search.return_value = [
        {"title": "x", "url": "https://example.com/", "snippet": "y"},
    ]
    mocker.patch(
        "src.research.findings_builder.ExaSearchClient",
        return_value=mock_exa,
    )
    mock_llm = MagicMock()
    mock_llm.chat.completions.create.side_effect = RuntimeError("llm boom")
    mocker.patch(
        "src.research.findings_builder.OpenAI",
        return_value=mock_llm,
    )
    mocker.patch(
        "src.research.findings_builder.settings.OPENROUTER_API_KEY",
        "test-openrouter-key",
    )

    s = create_session(rep_id="U1", account_name="Kroger")
    s.personas = ["csco"]
    respond = MagicMock()

    run_research(s, respond)
    respond.assert_called_once()


def test_build_placeholder_findings_still_callable_for_back_compat(mocker):
    """Phase 9 tests import build_placeholder_findings. We keep it as an
    alias that delegates to the real builder so legacy tests still pass."""
    from src.research.runner import build_placeholder_findings
    from src.research.sessions import create_session

    _mock_exa_and_llm(mocker)

    s = create_session(rep_id="U1", account_name="Kroger")
    s.personas = ["vp_warehouse_ops", "csco"]
    findings = build_placeholder_findings(s)

    assert findings["account_name"] == "Kroger"
    for k in ("trigger_events", "competitor_signals", "dc_intel",
              "board_initiatives", "research_gaps"):
        assert k in findings
