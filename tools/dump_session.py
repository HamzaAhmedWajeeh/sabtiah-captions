"""Dump the server's current captions as text, through the viewer socket.

    uv run python tools/dump_session.py
    uv run python tools/dump_session.py --url ws://127.0.0.1:8000/ws --json out.json

A live run leaves no capture file. `probe_soniox.py --file` writes JSONL
because it owns its own connection, but `/admin/start?source=live` streams
straight into `CaptionState`, and until Phase 6 adds `session_log.py` the only
copy of what was said is the server's memory.

This reads it the way a phone does: connect to `/ws`, take the snapshot the
hub sends on connect, print it. Read-only, no admin token, and it spends
nothing - the audience socket is the one endpoint that is already public.

Written on 2026-10-13 to recover the transcript of the first live run after
the fact. Keep it: it is also the quickest way to see what the room is
actually reading without leaning over someone's phone.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_URL = "ws://127.0.0.1:8000/ws"


def force_utf8_output() -> None:
    """Arabic to a redirected stdout is cp1252 on Windows otherwise."""
    import contextlib

    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.reconfigure(encoding="utf-8", errors="replace")


async def fetch(url: str, wait: float) -> dict:
    """Connect, take the snapshot, and optionally keep collecting diffs."""
    import websockets

    async with websockets.connect(url, max_size=None) as ws:
        snapshot = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        if snapshot.get("type") != "snapshot":
            raise SystemExit(f"expected a snapshot, got {snapshot.get('type')}")
        if wait <= 0:
            return snapshot

        # Fold later diffs in, so `--wait` can watch a run in progress.
        streams = {
            lang: {p["id"]: p for p in paragraphs}
            for lang, paragraphs in snapshot["streams"].items()
        }
        deadline = asyncio.get_running_loop().time() + wait
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                message = json.loads(
                    await asyncio.wait_for(ws.recv(), timeout=remaining)
                )
            except asyncio.TimeoutError:
                break
            if message.get("type") == "snapshot":
                streams = {
                    lang: {p["id"]: p for p in paragraphs}
                    for lang, paragraphs in message["streams"].items()
                }
                continue
            for lang, paragraphs in message.get("streams", {}).items():
                for para in paragraphs:
                    streams.setdefault(lang, {})[para["id"]] = para
            snapshot = {**message, "streams": {
                lang: sorted(byid.values(), key=lambda p: p["id"])
                for lang, byid in streams.items()
            }}
        return snapshot


def render(snapshot: dict, languages: list[str]) -> str:
    lines = [
        f"status: {snapshot.get('status')}   live: {snapshot.get('live')}   "
        f"seq: {snapshot.get('seq')}",
    ]
    for lang in languages:
        paragraphs = snapshot["streams"].get(lang, [])
        text = [(p["final"] + p["partial"]).strip() for p in paragraphs]
        text = [t for t in text if t]
        lines.append("")
        lines.append(f"=== {lang}  ({len(text)} paragraphs, "
                     f"{sum(len(t) for t in text)} chars) ===")
        for index, paragraph in enumerate(text, 1):
            lines.append(f"{index:3}. {paragraph}")
    return "\n".join(lines)


def main() -> int:
    force_utf8_output()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--lang", default="en,ar",
                        help="comma-separated streams to print")
    parser.add_argument("--wait", type=float, default=0.0,
                        help="seconds to keep collecting diffs (default 0)")
    parser.add_argument("--json", type=Path, default=None,
                        help="also write the raw snapshot here")
    args = parser.parse_args()

    try:
        snapshot = asyncio.run(fetch(args.url, args.wait))
    except OSError as exc:
        print(f"could not reach {args.url}: {exc}", file=sys.stderr)
        return 1

    if args.json:
        args.json.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(render(snapshot, [s.strip() for s in args.lang.split(",") if s.strip()]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
