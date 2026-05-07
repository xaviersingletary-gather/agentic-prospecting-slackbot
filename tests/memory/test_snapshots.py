"""V1.5 memory layer — snapshot store tests."""
from __future__ import annotations

import json
import os

import pytest

from src.memory import snapshots
from src.memory.snapshots import (
    SNAPSHOT_DIR_ENV,
    get_latest_snapshot,
    normalize_account_key,
    save_snapshot,
)


@pytest.fixture
def snap_dir(tmp_path, monkeypatch):
    d = tmp_path / "snaps"
    monkeypatch.setenv(SNAPSHOT_DIR_ENV, str(d))
    return d


def _findings(url="https://x.test/a"):
    return {
        "account_name": "PepsiCo",
        "trigger_events": [{"claim": "c", "source_url": url}],
        "competitor_signals": [],
        "dc_intel": [],
        "board_initiatives": [],
        "research_gaps": [],
    }


def test_normalize_strips_corporate_suffixes():
    assert normalize_account_key("PepsiCo, Inc.") == "pepsico"
    assert normalize_account_key("Acme Corp") == "acme"
    assert normalize_account_key("Globex LLC") == "globex"
    assert normalize_account_key("FooBar Limited") == "foobar"


def test_normalize_collapses_punctuation_and_lowercases():
    assert normalize_account_key("  J&J Worldwide!! ") == "j_j_worldwide"
    assert normalize_account_key("PEPSI-CO") == "pepsi_co"


def test_normalize_canonicalizes_inc_suffix_variants():
    assert (
        normalize_account_key("PepsiCo Inc")
        == normalize_account_key("PepsiCo, Inc.")
        == normalize_account_key("PepsiCo")
    )


def test_normalize_empty_returns_empty():
    assert normalize_account_key("") == ""
    assert normalize_account_key("   ") == ""
    assert normalize_account_key("!!!") == ""


def test_save_then_get_roundtrip(snap_dir):
    f = _findings()
    path = save_snapshot("PepsiCo", f)
    assert path is not None
    assert os.path.exists(path)

    snap = get_latest_snapshot("PepsiCo")
    assert snap is not None
    assert snap["account_name"] == "PepsiCo"
    assert snap["account_key"] == "pepsico"
    assert snap["findings"] == f
    assert "saved_at" in snap


def test_get_returns_most_recent_when_multiple(snap_dir):
    save_snapshot("PepsiCo", _findings(url="https://x.test/old"))
    save_snapshot("PepsiCo", _findings(url="https://x.test/new"))
    snap = get_latest_snapshot("PepsiCo")
    assert snap is not None
    urls = [it["source_url"] for it in snap["findings"]["trigger_events"]]
    assert urls == ["https://x.test/new"]


def test_get_returns_none_for_unknown_account(snap_dir):
    assert get_latest_snapshot("NeverResearched") is None


def test_get_skips_corrupt_trailing_line(snap_dir):
    save_snapshot("PepsiCo", _findings(url="https://x.test/good"))
    # Append a garbage line — get_latest should skip it and return the
    # last *parseable* record.
    key = normalize_account_key("PepsiCo")
    path = os.path.join(str(snap_dir), f"{key}.jsonl")
    with open(path, "a", encoding="utf-8") as f:
        f.write("not-json-at-all\n")
    snap = get_latest_snapshot("PepsiCo")
    assert snap is not None
    assert snap["findings"]["trigger_events"][0]["source_url"] == "https://x.test/good"


def test_save_skips_when_findings_not_dict(snap_dir):
    assert save_snapshot("PepsiCo", "not-a-dict") is None  # type: ignore[arg-type]


def test_save_skips_when_account_name_empty(snap_dir):
    assert save_snapshot("", _findings()) is None
