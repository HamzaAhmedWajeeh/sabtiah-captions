"""Phase 4: the Soniox spend ledger (`logs/usage.jsonl`).

The ledger exists because $10 has to cover the whole project (CLAUDE.md), so
the numbers on it have to be trustworthy in two directions: a billable run
must never be missed, and a free run must never be counted as spend. Both
failures are tested here, as is the one about the dollar estimate - an
unconfigured rate shows no dollars rather than a plausible-looking zero.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import usage  # noqa: E402
from app.settings import settings  # noqa: E402


def test_a_replay_run_is_logged_but_costs_nothing(tmp_path):
    usage.record_run(engine="replay", source="replay", audio_seconds=300.0,
                     detail="run1.jsonl", log_dir=tmp_path)
    totals = usage.totals(tmp_path)
    assert totals.runs == 1
    assert totals.billable_seconds == 0
    assert totals.free_seconds == 300.0
    assert totals.as_dict()["billable_minutes"] == 0.0
    assert totals.as_dict()["free_minutes"] == 5.0


def test_a_soniox_run_is_billable(tmp_path):
    usage.record_run(engine="soniox", source="mic", audio_seconds=61.6,
                     log_dir=tmp_path)
    totals = usage.totals(tmp_path)
    assert totals.billable_runs == 1
    assert totals.billable_seconds == 61.6
    assert totals.as_dict()["billable_minutes"] == 1.03


def test_billable_is_written_into_the_line(tmp_path):
    """The file has to be readable without knowing the engine names."""
    usage.record_run(engine="soniox", source="mic", audio_seconds=1.0,
                     log_dir=tmp_path)
    usage.record_run(engine="monitor", source="mic", audio_seconds=1.0,
                     log_dir=tmp_path)
    lines = [
        json.loads(line)
        for line in usage.usage_path(tmp_path).read_text(
            encoding="utf-8").splitlines()
    ]
    assert [row["billable"] for row in lines] == [True, False]
    assert all("ts" in row and row["ts"].endswith("Z") for row in lines)


def test_totals_of_a_missing_ledger_are_zero(tmp_path):
    totals = usage.totals(tmp_path / "nothing")
    assert totals.runs == 0
    assert totals.as_dict()["billable_minutes"] == 0.0
    assert totals.last is None


def test_a_corrupt_line_is_counted_not_fatal(tmp_path):
    path = usage.usage_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"audio_seconds": 60, "billable": true}\n'
        'not json at all\n'
        '{"no_seconds_here": 1}\n'
        '{"audio_seconds": 30, "billable": false}\n',
        encoding="utf-8",
    )
    totals = usage.totals(tmp_path)
    assert totals.runs == 2
    assert totals.unreadable_lines == 2
    assert totals.billable_seconds == 60
    assert totals.free_seconds == 30


def test_no_dollar_estimate_without_a_configured_rate(tmp_path, monkeypatch):
    """An invented rate printed to two decimals reads as a measurement."""
    monkeypatch.setattr(settings, "soniox_rate_per_minute", 0.0)
    usage.record_run(engine="soniox", source="mic", audio_seconds=600.0,
                     log_dir=tmp_path)
    shown = usage.totals(tmp_path).as_dict()
    assert shown["billable_minutes"] == 10.0
    assert shown["estimated_usd"] is None
    assert shown["budget_remaining_usd"] is None
    assert shown["rate_per_minute"] is None


def test_dollar_estimate_and_remainder_once_a_rate_is_set(tmp_path,
                                                          monkeypatch):
    monkeypatch.setattr(settings, "soniox_rate_per_minute", 0.10)
    monkeypatch.setattr(settings, "soniox_budget_usd", 10.0)
    usage.record_run(engine="soniox", source="mic", audio_seconds=1320.0,
                     log_dir=tmp_path)  # 22 minutes, the Phase 1 spend
    shown = usage.totals(tmp_path).as_dict()
    assert shown["billable_minutes"] == 22.0
    assert shown["estimated_usd"] == 2.2
    assert shown["budget_remaining_usd"] == 7.8


def test_recording_a_run_never_raises_on_an_unwritable_directory(tmp_path):
    """A session ending is the wrong moment to fail on a log write."""
    blocker = tmp_path / "logs"
    blocker.write_text("not a directory", encoding="utf-8")
    entry = usage.record_run(engine="replay", source="replay",
                             audio_seconds=1.0, log_dir=blocker)
    assert entry["audio_seconds"] == 1.0  # returned even though nothing wrote


def test_negative_seconds_cannot_credit_the_ledger(tmp_path):
    usage.record_run(engine="soniox", source="mic", audio_seconds=-500.0,
                     log_dir=tmp_path)
    assert usage.totals(tmp_path).billable_seconds == 0.0
