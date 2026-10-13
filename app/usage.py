"""The Soniox spend ledger.

CLAUDE.md: "Log audio seconds sent per run to `logs/usage.jsonl`, and show
the running total - minutes and estimated $ - on the admin page." $10 of
credit has to cover development, rehearsal and the live session, so the only
way to stay inside it is to be able to see it.

One JSON line per run, appended when the run ends:

    {"ts": "2026-10-13T12:00:00Z", "engine": "soniox", "source": "mic",
     "detail": "Microphone Array @ 48000 Hz -> 16000 Hz",
     "audio_seconds": 61.6, "billable": true, "note": ""}

`billable` is written explicitly rather than inferred from `engine`, because
the whole point of the file is to be readable a week later by someone asking
"where did the credit go" without having to know which engine names cost
money. Replay and mic monitoring are logged too, with `billable: false`: a
run that cost nothing is still evidence about what was tested, and a ledger
with gaps in it invites the question of whether a run was simply forgotten.

This file holds no caption text and no audio, only durations, so unlike
`session_log.py` (Phase 6) it is written whether or not logging is enabled.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.settings import LOG_DIR, settings

#: Engines whose seconds are charged to the Soniox balance. Everything else
#: in this project is deliberately free.
BILLABLE_ENGINES = {"soniox"}

USAGE_FILE = "usage.jsonl"


def usage_path(log_dir: Path | None = None) -> Path:
    return (log_dir or LOG_DIR) / USAGE_FILE


@dataclass
class Totals:
    """What `/admin` shows about spend."""

    runs: int = 0
    billable_runs: int = 0
    billable_seconds: float = 0.0
    free_seconds: float = 0.0
    #: The most recent run, for tooling. Deliberately **not** in
    #: `as_dict()`: it carries an engine name, and `/admin/status` is covered
    #: by a test that the served status mentions no engine at all.
    last: dict[str, Any] | None = None
    unreadable_lines: int = 0
    rate_per_minute: float = 0.0
    budget_usd: float = 0.0

    @property
    def billable_minutes(self) -> float:
        return self.billable_seconds / 60

    @property
    def estimated_usd(self) -> float | None:
        """None when no rate is configured.

        The Soniox per-minute price is not recorded anywhere in this repo and
        is not something to guess at: a made-up rate shown to two decimal
        places reads as a measurement. `SONIOX_RATE_PER_MINUTE` in `.env`
        turns the estimate on; until then the page shows minutes, which are
        counted rather than assumed.
        """
        if self.rate_per_minute <= 0:
            return None
        return self.billable_minutes * self.rate_per_minute

    def as_dict(self) -> dict[str, Any]:
        usd = self.estimated_usd
        return {
            "runs": self.runs,
            "billable_runs": self.billable_runs,
            "billable_minutes": round(self.billable_minutes, 2),
            "free_minutes": round(self.free_seconds / 60, 2),
            "estimated_usd": None if usd is None else round(usd, 2),
            "budget_usd": self.budget_usd or None,
            "budget_remaining_usd": (
                None if usd is None or not self.budget_usd
                else round(self.budget_usd - usd, 2)
            ),
            "rate_per_minute": self.rate_per_minute or None,
            "unreadable_lines": self.unreadable_lines,
        }


def record_run(
    *,
    engine: str,
    source: str,
    audio_seconds: float,
    detail: str = "",
    note: str = "",
    ts: str | None = None,
    log_dir: Path | None = None,
) -> dict[str, Any]:
    """Append one run to the ledger and return the line that was written.

    Never raises: a session ending is not the moment to fail because a log
    directory is read-only. A lost ledger line is worth less than a clean
    shutdown, and the failure is visible in the totals as a missing run.
    """
    entry = {
        # `ts` is overridable so spend from before this module existed can be
        # backfilled with the date it actually happened on, rather than the
        # date someone got round to writing it down.
        "ts": ts or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "engine": engine,
        "source": source,
        "detail": detail,
        "audio_seconds": round(max(0.0, audio_seconds), 1),
        "billable": engine in BILLABLE_ENGINES,
        "note": note,
    }
    path = usage_path(log_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return entry


def read_runs(log_dir: Path | None = None) -> tuple[list[dict[str, Any]], int]:
    """Every logged run, plus a count of lines that could not be parsed."""
    path = usage_path(log_dir)
    runs: list[dict[str, Any]] = []
    bad = 0
    if not path.exists():
        return runs, bad
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return runs, bad
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if isinstance(row, dict) and "audio_seconds" in row:
            runs.append(row)
        else:
            bad += 1
    return runs, bad


def totals(log_dir: Path | None = None) -> Totals:
    """Sum the ledger. Cheap enough to call on every status poll."""
    runs, bad = read_runs(log_dir)
    result = Totals(
        runs=len(runs),
        unreadable_lines=bad,
        last=runs[-1] if runs else None,
        rate_per_minute=float(getattr(settings, "soniox_rate_per_minute", 0.0)),
        budget_usd=float(getattr(settings, "soniox_budget_usd", 0.0)),
    )
    for row in runs:
        seconds = float(row.get("audio_seconds") or 0.0)
        if row.get("billable"):
            result.billable_runs += 1
            result.billable_seconds += seconds
        else:
            result.free_seconds += seconds
    return result
