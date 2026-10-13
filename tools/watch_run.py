"""Record a live run as it happens: captions, engine state, delay.

    uv run python tools/watch_run.py --token <ADMIN_TOKEN> --out docs/live/run7

Starts nothing and spends nothing - it watches a run the operator drives from
`/admin`. Two sources, one timeline:

- **`/ws`**, the same socket a phone uses, for every snapshot and diff with the
  wall-clock time it arrived. This is the transcript, and the paragraph-level
  timing.
- **`/admin/status`** at 2 Hz for `engine_state`, `lag_seconds`, `reconnects`
  and the input level. This is where a drop shows up, and how long it lasted.

Both run over loopback, so a Wi-Fi drop on the laptop does not interrupt the
recording - it only breaks the laptop's link to the engine, which is the thing
being measured.

Writes `<out>.jsonl` (every event, in order) and `<out>.json` (the final
caption snapshot), then prints a summary: term survival, reconnect windows and
delay percentiles. Ctrl-C, or `--seconds`, ends it.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

#: Terms whose survival in the Arabic stream is the point of the experiment.
#: Read from the glossary so this cannot drift from what was actually sent.
LATIN = re.compile(r"[A-Za-z][A-Za-z0-9.'\-]*")
ARABIC = re.compile(r"[؀-ۿ]")


def force_utf8_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.reconfigure(encoding="utf-8", errors="replace")


class Recorder:
    def __init__(self, out: Path) -> None:
        self.out = out
        out.parent.mkdir(parents=True, exist_ok=True)
        self.fh = out.with_suffix(".jsonl").open("w", encoding="utf-8")
        self.t0 = time.time()
        self.events: list[dict] = []

    def write(self, kind: str, payload: dict) -> dict:
        row = {
            "at": round(time.time() - self.t0, 3),
            "wall": time.strftime("%H:%M:%S"),
            "kind": kind,
            **payload,
        }
        self.fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        self.fh.flush()  # a run that ends in a crash still leaves its evidence
        self.events.append(row)
        return row

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.fh.close()


async def watch_socket(url: str, rec: Recorder, stop: asyncio.Event,
                       streams: dict) -> None:
    """Every snapshot and diff, with the time it arrived."""
    import websockets

    while not stop.is_set():
        try:
            async with websockets.connect(url, max_size=None) as ws:
                rec.write("socket", {"event": "connected"})
                while not stop.is_set():
                    raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    message = json.loads(raw)
                    if message.get("type") == "snapshot":
                        streams.clear()
                    for lang, paragraphs in message.get("streams", {}).items():
                        for para in paragraphs:
                            streams.setdefault(lang, {})[para["id"]] = para
                    # The full text of every changed paragraph, not just its
                    # id. run7 was recorded with ids only, and the transcript
                    # then existed in exactly one place - this process's
                    # memory - until it exited. It was recoverable that time
                    # because the server still held the state; a crash, or a
                    # second run started on top, and it would have been gone.
                    rec.write("feed", {
                        "type": message.get("type"),
                        "seq": message.get("seq"),
                        "live": message.get("live"),
                        "status": message.get("status"),
                        "changed": {
                            lang: paragraphs
                            for lang, paragraphs in
                            message.get("streams", {}).items() if paragraphs
                        },
                    })
        except asyncio.TimeoutError:
            continue
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if stop.is_set():
                return
            rec.write("socket", {"event": "lost", "error": str(exc)})
            await asyncio.sleep(0.5)


async def watch_status(base: str, token: str, rec: Recorder,
                       stop: asyncio.Event) -> None:
    """`/admin/status` at 2 Hz: the engine's own view."""
    import urllib.parse
    import urllib.request

    url = base + "/admin/status?" + urllib.parse.urlencode({"token": token})
    loop = asyncio.get_running_loop()
    while not stop.is_set():
        try:
            body = await loop.run_in_executor(
                None,
                lambda: urllib.request.urlopen(url, timeout=3).read(),
            )
            status = json.loads(body.decode("utf-8"))
            rec.write("status", {
                k: status.get(k) for k in (
                    "running", "mode", "engine_state", "lag_seconds",
                    "reconnects", "rollovers", "keepalives", "pauses",
                    "streamed_seconds", "silent_seconds", "responses",
                    "viewers", "error", "engine_error", "level",
                )
            })
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            rec.write("status", {"error": f"{type(exc).__name__}: {exc}"})
        await asyncio.sleep(0.5)


def summarise(rec: Recorder, streams: dict) -> str:
    from app.settings import soniox_context

    lines: list[str] = []
    say = lines.append

    statuses = [e for e in rec.events if e["kind"] == "status"]
    feeds = [e for e in rec.events if e["kind"] == "feed"]
    running = [s for s in statuses if s.get("running")]

    say("=" * 66)
    say(f"recorded {rec.events[-1]['at'] if rec.events else 0:.1f} s, "
        f"{len(feeds)} feed messages, {len(statuses)} status polls")
    if running:
        last = running[-1]

        def shown(key, suffix=""):
            # A replay has no engine, so these are absent rather than zero.
            # Printing "None" in a report invites the reader to wonder which
            # it was.
            value = last.get(key)
            return "-" if value is None else f"{value}{suffix}"

        say(f"streamed {shown('streamed_seconds', ' s')}  "
            f"responses {shown('responses')}  "
            f"reconnects {shown('reconnects')}  "
            f"rollovers {shown('rollovers')}  "
            f"pauses {shown('pauses')}")
        if last.get("engine_error"):
            say(f"engine error: {last['engine_error']}")

    # -- engine state timeline --------------------------------------------
    say("")
    say("engine state changes")
    previous = None
    changes = []
    for row in statuses:
        state = row.get("engine_state")
        if state is None:
            continue  # a replay has no engine at all
        if state != previous:
            changes.append((row["at"], state))
            say(f"  {row['at']:7.1f}s  {previous or 'start'} -> {state}")
            previous = state
    if not changes:
        say("  (none recorded - no live engine ran)")
    for index, (at, state) in enumerate(changes):
        if state == "reconnecting":
            back = next((a for a, s in changes[index + 1:]
                         if s in ("streaming", "connecting")), None)
            if back is not None:
                say(f"  ** outage recovered in {back - at:.1f} s **")

    # -- delay -------------------------------------------------------------
    lags = [s["lag_seconds"] for s in running
            if isinstance(s.get("lag_seconds"), (int, float))]
    if lags:
        ordered = sorted(lags)
        say("")
        say("caption delay (audio sent but not yet acknowledged)")
        say(f"  median {ordered[len(ordered) // 2]:.2f} s   "
            f"p90 {ordered[int(len(ordered) * 0.9)]:.2f} s   "
            f"max {ordered[-1]:.2f} s")

    # -- terms -------------------------------------------------------------
    context = soniox_context()
    wanted = [t["source"] for t in context["translation_terms"]
              if t["source"] == t["target"]]
    english = " ".join((p["final"] + p["partial"])
                       for p in streams.get("en", {}).values())
    arabic = " ".join((p["final"] + p["partial"])
                      for p in streams.get("ar", {}).values())

    say("")
    say("translation_terms: did the English word survive into the Arabic?")
    say(f"  {'term':<16}{'said (en)':<12}{'kept (ar)'}")
    for term in wanted:
        said = bool(re.search(r"\b" + re.escape(term) + r"\b", english, re.I))
        kept = bool(re.search(r"\b" + re.escape(term) + r"\b", arabic, re.I))
        mark = "-" if not said else ("YES" if kept else "no")
        say(f"  {term:<16}{'yes' if said else 'no':<12}{mark}")

    latin = sorted({m.group(0) for m in LATIN.finditer(arabic)
                    if len(m.group(0)) > 1})
    say("")
    say(f"all Latin-script runs surviving in the ar stream: {latin or 'NONE'}")
    say(f"Arabic script present in the en stream: "
        f"{'yes - LEAK' if ARABIC.search(english) else 'no'}")
    say("")
    say(f"en {len(streams.get('en', {}))} paragraphs, "
        f"ar {len(streams.get('ar', {}))} paragraphs")
    say("=" * 66)
    return "\n".join(lines)


async def main_async(args) -> int:
    rec = Recorder(Path(args.out))
    streams: dict[str, dict] = {}
    stop = asyncio.Event()

    base = args.url.rstrip("/")
    socket_url = base.replace("http://", "ws://").replace("https://", "wss://")
    tasks = [
        asyncio.create_task(watch_socket(socket_url + "/ws", rec, stop, streams)),
        asyncio.create_task(watch_status(base, args.token, rec, stop)),
    ]

    print(f"recording to {Path(args.out).with_suffix('.jsonl')}")
    print("start the run from /admin now. Ctrl-C when it is done.\n")
    try:
        if args.seconds:
            await asyncio.sleep(args.seconds)
        else:
            await asyncio.Event().wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        stop.set()
        for task in tasks:
            task.cancel()
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await task

    snapshot = {lang: sorted(byid.values(), key=lambda p: p["id"])
                for lang, byid in streams.items()}
    Path(args.out).with_suffix(".json").write_text(
        json.dumps({"streams": snapshot}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    # Plain text next to the JSON: the thing a human actually reads when
    # asking "what did it say".
    lines = []
    for lang, paragraphs in snapshot.items():
        lines.append(f"=== {lang} ===")
        for index, para in enumerate(paragraphs, 1):
            text = (para["final"] + para["partial"]).strip()
            if text:
                lines.append(f"{index:3}. {text}")
        lines.append("")
    Path(args.out).with_suffix(".transcript.txt").write_text(
        "\n".join(lines), encoding="utf-8")
    report = summarise(rec, streams)
    print(report)
    Path(args.out).with_suffix(".txt").write_text(report, encoding="utf-8")
    rec.close()
    return 0


def main() -> int:
    force_utf8_output()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--token", required=True, help="ADMIN_TOKEN")
    parser.add_argument("--out", default="docs/live/run7")
    parser.add_argument("--seconds", type=float, default=None,
                        help="stop after this long (default: until Ctrl-C)")
    args = parser.parse_args()
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
