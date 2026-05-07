"""V1.5 memory layer — diff logic tests."""
from __future__ import annotations

from src.memory.diff import diff_findings, diff_is_empty


def _f(**sections):
    base = {
        "trigger_events": [],
        "competitor_signals": [],
        "dc_intel": [],
        "board_initiatives": [],
        "research_gaps": [],
    }
    base.update(sections)
    return base


def _item(claim, url):
    return {"claim": claim, "source_url": url}


def test_cold_start_returns_empty_diff():
    new = _f(trigger_events=[_item("c1", "https://x.test/1")])
    diff = diff_findings(None, new)
    assert diff_is_empty(diff)


def test_no_overlap_all_items_are_new():
    prev = _f(trigger_events=[_item("old", "https://x.test/old")])
    new = _f(trigger_events=[_item("new", "https://x.test/new")])
    diff = diff_findings(prev, new)
    assert diff["trigger_events"] == [_item("new", "https://x.test/new")]
    assert not diff_is_empty(diff)


def test_overlap_drops_already_seen_urls():
    prev = _f(competitor_signals=[
        _item("seen", "https://x.test/seen"),
    ])
    new = _f(competitor_signals=[
        _item("seen", "https://x.test/seen"),
        _item("fresh", "https://x.test/fresh"),
    ])
    diff = diff_findings(prev, new)
    assert diff["competitor_signals"] == [_item("fresh", "https://x.test/fresh")]


def test_identical_findings_yield_empty_diff():
    f = _f(
        trigger_events=[_item("a", "https://x.test/a")],
        board_initiatives=[_item("b", "https://x.test/b")],
    )
    diff = diff_findings(f, f)
    assert diff_is_empty(diff)


def test_diff_runs_per_section_independently():
    prev = _f(
        trigger_events=[_item("t-old", "https://x.test/t-old")],
        dc_intel=[_item("d-old", "https://x.test/d-old")],
    )
    new = _f(
        trigger_events=[_item("t-old", "https://x.test/t-old")],   # repeat
        dc_intel=[_item("d-new", "https://x.test/d-new")],          # new
        board_initiatives=[_item("b-new", "https://x.test/b-new")], # new
    )
    diff = diff_findings(prev, new)
    assert diff["trigger_events"] == []
    assert diff["dc_intel"] == [_item("d-new", "https://x.test/d-new")]
    assert diff["board_initiatives"] == [_item("b-new", "https://x.test/b-new")]


def test_research_gaps_are_not_diffed():
    prev = _f()
    new = _f()
    new["research_gaps"] = ["something the bot couldn't find"]
    diff = diff_findings(prev, new)
    assert "research_gaps" not in diff
    assert diff_is_empty(diff)


def test_items_without_url_or_claim_are_dropped():
    prev = _f()
    new = _f(trigger_events=[
        {"claim": "no url"},
        {"source_url": "https://x.test/no-claim"},
        _item("good", "https://x.test/good"),
    ])
    diff = diff_findings(prev, new)
    assert diff["trigger_events"] == [_item("good", "https://x.test/good")]


def test_malformed_inputs_yield_empty_diff():
    assert diff_is_empty(diff_findings(None, None))
    assert diff_is_empty(diff_findings("not-a-dict", _f()))  # type: ignore[arg-type]
    assert diff_is_empty(diff_findings(_f(), "not-a-dict"))  # type: ignore[arg-type]
