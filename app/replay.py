"""Replay a saved Soniox capture instead of calling Soniox.

Soniox credit is $10 for the whole project (CLAUDE.md), so caption logic, UI,
broadcast and reconnect work are all developed against recorded JSONL rather
than live audio. This module is the seam: `FakeSonioxClient` yields the same
response dicts a real connection would, read from `tests/fixtures/*.jsonl` or
`docs/captures/*.jsonl`.

A capture line is `{recv_at, recv_offset_ms, sent_ms, response}`, with the
first line carrying `{recv_at, meta}` instead. `recv_offset_ms` is wall-clock
milliseconds from stream start, which is what makes `paced=True` reproduce the
original timing - including the ~0.65 s caption lag - so the audience page can
be judged at real speed.

Nothing here imports `websockets` or touches the network, and the default
`paced=False` runs a five-minute capture in well under a second.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path


def load_capture(path: str | Path) -> tuple[dict, list[dict]]:
    """Return `(meta, records)` for a capture file.

    `records` keeps the envelope, not just the response, because
    `recv_offset_ms` is needed for paced replay and for latency assertions.
    """
    meta: dict = {}
    records: list[dict] = []
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if "meta" in row:
                meta = row["meta"]
            elif "response" in row:
                records.append(row)
    return meta, records


def responses(path: str | Path) -> Iterator[dict]:
    """Just the raw Soniox response dicts, in order. For synchronous tests."""
    _, records = load_capture(path)
    for row in records:
        yield row["response"]


class FakeSonioxClient:
    """Stands in for `soniox_client.SonioxClient` over a recorded capture.

    Same shape as the real thing will have in Phase 5 - an async iterator of
    response dicts - so `captions.CaptionState` and the Phase 3 broadcast hub
    cannot tell the difference.
    """

    def __init__(self, path: str | Path, *, paced: bool = False) -> None:
        self.path = Path(path)
        self.paced = paced
        self.meta, self._records = load_capture(self.path)
        self.sent_ms = 0

    def __len__(self) -> int:
        return len(self._records)

    @property
    def audio_seconds(self) -> float:
        """Audio the capture represents. Replay spends none of it on Soniox."""
        if not self._records:
            return 0.0
        return max(r.get("sent_ms", 0) for r in self._records) / 1000

    async def __aiter__(self):
        start = time.perf_counter()
        for row in self._records:
            if self.paced:
                target = row.get("recv_offset_ms", 0) / 1000
                delay = target - (time.perf_counter() - start)
                if delay > 0:
                    await asyncio.sleep(delay)
            self.sent_ms = row.get("sent_ms", self.sent_ms)
            yield row["response"]


class CaptureWriter:
    """Write a live run to the same JSONL format `load_capture` reads.

    The counterpart of everything above, and the missing half of the loop: a
    live run used to leave no token-level record at all, so a question like
    "did Soniox really label that Arabic phrase `en`?" could not be answered
    after the fact (run7, 2026-10-13). A run captured here can be replayed
    through `FakeSonioxClient`, dropped into `tests/fixtures/` and turned into
    a regression test, which is how every other finding in this project became
    checkable.

    Off by default, because a capture is a verbatim transcript of whatever was
    said in the room (CLAUDE.md: logging off unless the operator turns it on).
    Lands in `logs/`, which is gitignored and not servable.
    """

    def __init__(self, path: str | Path, meta: dict | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.responses = 0
        self._t0 = time.perf_counter()
        self._fh = self.path.open("w", encoding="utf-8")
        self._write({
            "recv_at": datetime.now(timezone.utc).isoformat(),
            "meta": meta or {},
        })

    def _write(self, row: dict) -> None:
        self._fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        # Flushed per line: a run that ends in a crash is exactly the run
        # whose capture is worth having.
        self._fh.flush()

    def add(self, response: dict, *, sent_ms: int = 0) -> None:
        self.responses += 1
        self._write({
            "recv_at": datetime.now(timezone.utc).isoformat(),
            "recv_offset_ms": round((time.perf_counter() - self._t0) * 1000),
            "sent_ms": sent_ms,
            "response": response,
        })

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass
