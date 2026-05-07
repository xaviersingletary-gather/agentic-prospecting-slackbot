"""V1.5 memory layer — runner wiring smoke tests.

Verifies that `_build_account_blocks` prepends the "🆕 New since" stack
when a prior snapshot exists and is empty-prepended on cold start.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.memory.snapshots import SNAPSHOT_DIR_ENV, save_snapshot
from src.research import runner
from src.research.sessions import ResearchSession


@pytest.fixture
def snap_dir(tmp_path, monkeypatch):
    d = tmp_path / "snaps"
    monkeypatch.setenv(SNAPSHOT_DIR_ENV, str(d))
    return d


def _session(name="PepsiCo"):
    return ResearchSession(session_id="sess-1", rep_id="U1", account_name=name)


def _findings(*urls):
    return {
        "account_name": "PepsiCo",
        "trigger_events": [
            {"claim": f"c-{i}", "source_url": u} for i, u in enumerate(urls)
        ],
        "competitor_signals": [],
        "dc_intel": [],
        "board_initiatives": [],
        "research_gaps": [],
    }


def _block_text(blocks):
    out = []
    for b in blocks:
        if b.get("type") == "header":
            out.append(b["text"]["text"])
        elif b.get("type") == "section":
            t = b.get("text", {}).get("text", "")
            if t:
                out.append(t)
    return "\n".join(out)


def test_cold_start_no_new_since_block(snap_dir):
    """First-ever research for an account → no "New since" header."""
    with patch.object(runner, "build_findings",
                      return_value=_findings("https://x.test/1")):
        blocks = runner._build_account_blocks(_session())
    assert "New since" not in _block_text(blocks)


def test_second_run_prepends_new_since_for_new_urls(snap_dir):
    save_snapshot("PepsiCo", _findings("https://x.test/old"))
    with patch.object(
        runner, "build_findings",
        return_value=_findings("https://x.test/old", "https://x.test/new"),
    ):
        blocks = runner._build_account_blocks(_session())
    text = _block_text(blocks)
    assert "🆕 New since" in text
    assert "c-1" in text  # the new claim
    # The old claim is in the standard research blocks too, but the
    # "New since" header must come before that section.
    new_since_idx = next(
        i for i, b in enumerate(blocks)
        if b.get("type") == "header"
        and "New since" in b.get("text", {}).get("text", "")
    )
    main_header_idx = next(
        i for i, b in enumerate(blocks)
        if b.get("type") == "header"
        and "🏢" in b.get("text", {}).get("text", "")
    )
    assert new_since_idx < main_header_idx


def test_second_run_with_no_new_urls_has_no_new_since_block(snap_dir):
    save_snapshot("PepsiCo", _findings("https://x.test/same"))
    with patch.object(runner, "build_findings",
                      return_value=_findings("https://x.test/same")):
        blocks = runner._build_account_blocks(_session())
    assert "New since" not in _block_text(blocks)


def test_run_persists_new_snapshot(snap_dir):
    from src.memory.snapshots import get_latest_snapshot

    with patch.object(runner, "build_findings",
                      return_value=_findings("https://x.test/persist")):
        runner._build_account_blocks(_session())

    snap = get_latest_snapshot("PepsiCo")
    assert snap is not None
    urls = [it["source_url"] for it in snap["findings"]["trigger_events"]]
    assert urls == ["https://x.test/persist"]


def test_memory_failure_does_not_break_research(snap_dir, monkeypatch):
    """Even if every memory call raises, research blocks still render."""
    def boom(*a, **kw):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr("src.research.runner.get_latest_snapshot", boom)
    monkeypatch.setattr("src.research.runner.save_snapshot", boom)
    monkeypatch.setattr("src.research.runner.diff_findings", boom)

    with patch.object(runner, "build_findings",
                      return_value=_findings("https://x.test/ok")):
        blocks = runner._build_account_blocks(_session())

    # Standard research blocks still rendered (header + sections).
    text = _block_text(blocks)
    assert "🏢" in text
    assert "New since" not in text
