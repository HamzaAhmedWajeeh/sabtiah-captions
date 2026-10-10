"""Phase 1 probe: stream audio to Soniox and capture every raw response.

Two modes:

  stream   decode an audio file with ffmpeg, send it at real-time pace, write
           every raw response to JSONL, and print a readable rendering
  analyze  read a captured JSONL and answer the five questions in
           BUILD_SPEC.md section 4

    uv run python tools/probe_soniox.py --file talk.m4a --out docs/captures/run1.jsonl
    uv run python tools/probe_soniox.py --analyze docs/captures/run1.jsonl

Timing note: audio is paced at real time, so the audio at offset X ms leaves
the laptop at roughly X ms after stream start. Every captured response carries
`recv_offset_ms` on the same clock, which makes `recv_offset_ms - token.end_ms`
a usable wall-clock latency.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.settings import load_glossary, load_session, settings  # noqa: E402

WS_URL = "wss://stt-rt.soniox.com/transcribe-websocket"
SAMPLE_RATE = 16000
BYTES_PER_MS = SAMPLE_RATE * 2 // 1000  # mono int16
CHUNK_MS = 100
CHUNK_BYTES = CHUNK_MS * BYTES_PER_MS
END_TOKEN = "<end>"

ARABIC = re.compile(r"[؀-ۿ]")
LATIN = re.compile(r"[A-Za-z]")

BASE_CONFIG: dict = {
    "model": "stt-rt-v5",
    "audio_format": "pcm_s16le",
    "sample_rate": SAMPLE_RATE,
    "num_channels": 1,
    "language_hints": ["ar", "en"],
    "enable_language_identification": True,
    "enable_speaker_diarization": False,
    "enable_endpoint_detection": True,
    "context": {"general": [], "text": "", "terms": [], "translation_terms": []},
    "translation": {"type": "two_way", "language_a": "ar", "language_b": "en"},
}


def build_context(use_glossary: bool) -> dict:
    """Soniox `context` from config/glossary.yaml + config/session.yaml."""
    if not use_glossary:
        return {"general": [], "text": "", "terms": [], "translation_terms": []}
    glossary = load_glossary()
    session = load_session()
    general = []
    if session.title:
        general.append({"key": "title", "value": session.title})
    for name in session.speakers:
        general.append({"key": "speaker", "value": name})
    for company in session.companies:
        general.append({"key": "company", "value": company})
    return {
        "general": general,
        "text": session.notes,
        "terms": glossary.terms,
        "translation_terms": glossary.translation_terms,
    }


# ---------------------------------------------------------------------------
# stream mode
# ---------------------------------------------------------------------------


@dataclass
class StreamState:
    t0: float = 0.0
    sent_ms: int = 0
    responses: int = 0
    finished: bool = False
    error: dict | None = None
    finals: dict[str, str] = field(default_factory=lambda: defaultdict(str))
    last_partial: dict[str, str] = field(default_factory=lambda: defaultdict(str))

    def elapsed_ms(self) -> int:
        return round((time.perf_counter() - self.t0) * 1000)


async def ffmpeg_pcm(path: Path) -> asyncio.subprocess.Process:
    """Decode anything ffmpeg understands to 16 kHz mono s16le on stdout."""
    return await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(path),
        "-f",
        "s16le",
        "-acodec",
        "pcm_s16le",
        "-ac",
        "1",
        "-ar",
        str(SAMPLE_RATE),
        "-",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


def stream_key(token: dict) -> str:
    """Label a token by the stream it belongs to, for the readable rendering."""
    if token.get("translation_status") == "translation":
        return "TR " + str(token.get("language", "?"))
    return "OR"


def render(state: StreamState, tokens: list[dict]) -> None:
    """Print finals solid and non-finals in brackets, as they arrive.

    Final tokens arrive once, so they are appended. Non-final tokens are
    re-sent in full on every response, so the partial buffer is replaced.
    """
    appended: dict[str, str] = defaultdict(str)
    partial: dict[str, str] = defaultdict(str)
    ended: set[str] = set()

    for token in tokens:
        key = stream_key(token)
        text = token.get("text", "")
        if text == END_TOKEN:
            ended.add(key)
            continue
        if token.get("is_final"):
            appended[key] += text
        else:
            partial[key] += text

    stamp = "[{:7.2f}s]".format(state.elapsed_ms() / 1000)

    for key, text in appended.items():
        state.finals[key] += text
        if text.strip():
            print("{} {:<6} {}".format(stamp, key, text.strip()))

    for key in sorted(set(partial) | set(state.last_partial)):
        text = partial.get(key, "")
        if text != state.last_partial[key]:
            state.last_partial[key] = text
            if text.strip():
                print("{} {:<6} [{}]".format(stamp, key, text.strip()))

    for key in sorted(ended):
        print("{} {:<6} <END>".format(stamp, key))


async def receive_loop(ws, state: StreamState, out_fh) -> None:
    async for raw in ws:
        if isinstance(raw, bytes):  # server should never send binary
            continue
        msg = json.loads(raw)
        state.responses += 1
        record = {
            "recv_at": datetime.now(timezone.utc).isoformat(),
            "recv_offset_ms": state.elapsed_ms(),
            "sent_ms": state.sent_ms,
            "response": msg,
        }
        out_fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        out_fh.flush()

        if msg.get("error_type") or msg.get("error_code"):
            state.error = msg
            print(
                "\nERROR {} ({}): {}  request_id={}".format(
                    msg.get("error_type"),
                    msg.get("error_code"),
                    msg.get("error_message"),
                    msg.get("request_id"),
                ),
                file=sys.stderr,
            )
            return
        if msg.get("finished"):
            state.finished = True
            print("\n[{:7.2f}s] finished".format(state.elapsed_ms() / 1000))
            return
        render(state, msg.get("tokens") or [])


async def send_loop(ws, proc, state: StreamState) -> None:
    index = 0
    while True:
        try:
            chunk = await proc.stdout.readexactly(CHUNK_BYTES)
        except asyncio.IncompleteReadError as exc:
            chunk = exc.partial
            if chunk:
                await ws.send(chunk)
                state.sent_ms += len(chunk) // BYTES_PER_MS
            break
        await ws.send(chunk)
        state.sent_ms += CHUNK_MS
        index += 1
        # pace at real time so recv offsets are comparable to audio offsets
        target = state.t0 + index * (CHUNK_MS / 1000)
        delay = target - time.perf_counter()
        if delay > 0:
            await asyncio.sleep(delay)

    await ws.send("")  # graceful end of stream
    print(
        "[{:7.2f}s] sent end-of-stream ({:.1f}s of audio)".format(
            state.elapsed_ms() / 1000, state.sent_ms / 1000
        )
    )


async def run_stream(args: argparse.Namespace) -> int:
    if not settings.soniox_api_key:
        print(
            "SONIOX_API_KEY is empty. Copy .env.example to .env and set it.",
            file=sys.stderr,
        )
        return 2

    audio = Path(args.file)
    if not audio.exists():
        print("No such audio file: {}".format(audio), file=sys.stderr)
        return 2

    config = dict(BASE_CONFIG)
    config["context"] = build_context(not args.no_glossary)
    if args.config:
        override = json.loads(Path(args.config).read_text(encoding="utf-8"))
        config.update(override)  # shallow: replaces whole keys like "translation"

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    proc = await ffmpeg_pcm(audio)
    state = StreamState()

    print("config: " + json.dumps(config, ensure_ascii=False))
    print("audio:  {}".format(audio))
    print("out:    {}\n".format(out_path))

    with out_path.open("w", encoding="utf-8") as out_fh:
        out_fh.write(
            json.dumps(
                {
                    "recv_at": datetime.now(timezone.utc).isoformat(),
                    "meta": {
                        "audio": str(audio),
                        "config": config,
                        "chunk_ms": CHUNK_MS,
                    },
                },
                ensure_ascii=False,
            )
            + "\n"
        )
        async with websockets.connect(
            WS_URL,
            additional_headers={
                "Authorization": "Bearer " + settings.soniox_api_key
            },
            max_size=None,
            ping_interval=20,
        ) as ws:
            state.t0 = time.perf_counter()
            await ws.send(json.dumps(config))
            receiver = asyncio.create_task(receive_loop(ws, state, out_fh))
            sender = asyncio.create_task(send_loop(ws, proc, state))
            await sender
            try:  # server flushes remaining tokens, then {"finished": true}
                await asyncio.wait_for(receiver, timeout=60)
            except asyncio.TimeoutError:
                receiver.cancel()
                print("timed out waiting for `finished`", file=sys.stderr)

    stderr = (await proc.stderr.read()).decode(errors="replace").strip()
    await proc.wait()
    if stderr:
        print("ffmpeg: " + stderr, file=sys.stderr)

    print("\n{} responses -> {}".format(state.responses, out_path))
    if state.error:
        return 1
    return 0 if state.finished else 1


# ---------------------------------------------------------------------------
# analyze mode
# ---------------------------------------------------------------------------


@dataclass
class Seen:
    """A final token, with the wall-clock offset at which it first arrived."""

    text: str
    recv_offset_ms: int
    language: str | None = None
    source_language: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    confidence: float | None = None
    translation_status: str | None = None


@dataclass
class Segment:
    """Original-side segment, bounded by an `<end>` final token."""

    index: int
    orig: list[Seen] = field(default_factory=list)
    translation: list[Seen] = field(default_factory=list)
    end_recv_ms: int | None = None
    tr_end_recv_ms: int | None = None

    def orig_text(self) -> str:
        return "".join(t.text for t in self.orig).strip()

    def tr_text(self) -> str:
        return "".join(t.text for t in self.translation).strip()

    def last_orig_end_ms(self) -> int | None:
        values = [t.end_ms for t in self.orig if t.end_ms is not None]
        return max(values) if values else None


def load_capture(path: Path) -> tuple[dict, list[dict]]:
    meta: dict = {}
    records: list[dict] = []
    with path.open(encoding="utf-8") as fh:
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


def build_segments(records: list[dict]) -> tuple[list[Segment], list[dict]]:
    """Walk the capture, grouping final tokens into endpoint-bounded segments.

    An original `<end>` closes the current segment. Translation tokens are
    attached to whichever segment was open on the original side when they
    arrived, which is exactly what question 1 is about: tokens landing after
    their segment's `<end>` show up attached to a closed segment.
    """
    segments: list[Segment] = [Segment(index=0)]
    errors: list[dict] = []

    for row in records:
        msg = row["response"]
        if msg.get("error_type") or msg.get("error_code"):
            errors.append(msg)
            continue
        recv = row["recv_offset_ms"]
        for token in msg.get("tokens") or []:
            if not token.get("is_final"):
                continue  # non-finals are re-sent; only finals are evidence
            status = token.get("translation_status", "none")
            text = token.get("text", "")
            seen = Seen(
                text=text,
                recv_offset_ms=recv,
                language=token.get("language"),
                source_language=token.get("source_language"),
                start_ms=token.get("start_ms"),
                end_ms=token.get("end_ms"),
                confidence=token.get("confidence"),
                translation_status=status,
            )
            is_translation = status == "translation"

            if text == END_TOKEN:
                if is_translation:
                    segments[-1].tr_end_recv_ms = recv
                else:
                    segments[-1].end_recv_ms = recv
                    segments.append(Segment(index=len(segments)))
                continue

            if is_translation:
                # attach to the newest segment that still has original text
                target = segments[-1]
                if not target.orig and len(segments) > 1:
                    target = segments[-2]
                target.translation.append(seen)
            else:
                segments[-1].orig.append(seen)

    if not segments[-1].orig and not segments[-1].translation:
        segments.pop()
    return segments, errors


def script_mix(text: str) -> str:
    has_ar = bool(ARABIC.search(text))
    has_la = bool(LATIN.search(text))
    if has_ar and has_la:
        return "mixed"
    if has_ar:
        return "arabic"
    if has_la:
        return "latin"
    return "other"


def summarize(path: Path) -> str:
    """Answer BUILD_SPEC section 4 from a capture. Returns a markdown report."""
    meta, records = load_capture(path)
    segments, errors = build_segments(records)
    out: list[str] = []

    def say(line: str = "") -> None:
        out.append(line)

    say("# Soniox probe analysis: `{}`".format(path.name))
    say()
    say("- audio: `{}`".format(meta.get("audio", "?")))
    say("- responses: {}".format(len(records)))
    say("- endpoint-bounded segments: {}".format(len(segments)))
    if errors:
        say("- errors: {}".format(
            ", ".join(str(e.get("error_type")) for e in errors)))
    say()

    # -- Q1: do translation tokens arrive after their segment's <end>? -------
    say("## Q1. Translation tokens arriving after the segment's `<end>`")
    say()
    late: list[int] = []
    rows = []
    for seg in segments:
        if seg.end_recv_ms is None or not seg.translation:
            continue
        after = [t for t in seg.translation if t.recv_offset_ms > seg.end_recv_ms]
        if after:
            lag = max(t.recv_offset_ms for t in after) - seg.end_recv_ms
            late.append(lag)
        rows.append((seg.index, len(seg.translation), len(after),
                     (max(t.recv_offset_ms for t in after) - seg.end_recv_ms)
                     if after else 0))
    if rows:
        say("| segment | translation tokens | arrived after `<end>` | last one (ms after `<end>`) |")
        say("| --- | --- | --- | --- |")
        for index, total, after_n, lag in rows:
            say("| {} | {} | {} | {} |".format(index, total, after_n, lag))
        say()
        say("Segments whose translation finished only after `<end>`: "
            "**{} of {}**.".format(len(late), len(rows)))
        if late:
            say("Lateness ms: min {}, median {}, max {}.".format(
                min(late), round(statistics.median(late)), max(late)))
    else:
        say("_No segment had both an `<end>` and translation tokens._")
    say()

    # -- Q2: end marker on the translation side? ----------------------------
    say("## Q2. Is there an end marker on the translation side?")
    say()
    tr_ends = sum(1 for s in segments if s.tr_end_recv_ms is not None)
    say("`<end>` tokens carrying `translation_status: \"translation\"`: "
        "**{}**.".format(tr_ends))
    say()

    # -- Q3: language on mixed-sentence original tokens ---------------------
    say("## Q3. Token languages in mixed sentences")
    say()
    mixed = [s for s in segments if script_mix(s.orig_text()) == "mixed"]
    say("Segments whose original text mixes Arabic and Latin script: "
        "**{} of {}**.".format(len(mixed), len(segments)))
    say()
    for seg in mixed[:6]:
        say("**Segment {}** — original: `{}`".format(seg.index, seg.orig_text()))
        say()
        langs = Counter(t.language for t in seg.orig)
        say("- token languages: {}".format(dict(langs)))
        latin_tokens = [t for t in seg.orig if LATIN.search(t.text)]
        if latin_tokens:
            say("- Latin-script tokens and their `language`: {}".format(
                ", ".join("`{}`={}".format(t.text.strip(), t.language)
                          for t in latin_tokens if t.text.strip())))
        say("- translation: `{}`".format(seg.tr_text()))
        say("- translation keeps Latin script: {}".format(
            bool(LATIN.search(seg.tr_text()))))
        say()
    if not mixed:
        say("_No mixed-script segment in this capture._")
        say()

    say("Per-token `language` distribution across all original final tokens:")
    say()
    say("```")
    say(str(dict(Counter(t.language for s in segments for t in s.orig))))
    say("```")
    say()

    # -- Q4: source_language on translation tokens --------------------------
    say("## Q4. `source_language` on translation tokens")
    say()
    pairs = Counter(
        (t.source_language, t.language)
        for s in segments
        for t in s.translation
    )
    if pairs:
        say("| source_language | language | tokens |")
        say("| --- | --- | --- |")
        for (src, lang), count in pairs.most_common():
            say("| `{}` | `{}` | {} |".format(src, lang, count))
    else:
        say("_No translation tokens in this capture._")
    say()

    # -- Q5: end_ms to translation arrival ----------------------------------
    say("## Q5. Delay from a word's `end_ms` to its translation arriving")
    say()
    first_lag: list[int] = []
    last_lag: list[int] = []
    orig_lag: list[int] = []
    for seg in segments:
        for token in seg.orig:
            if token.end_ms is not None:
                orig_lag.append(token.recv_offset_ms - token.end_ms)
        end_ms = seg.last_orig_end_ms()
        if end_ms is None or not seg.translation:
            continue
        first_lag.append(min(t.recv_offset_ms for t in seg.translation) - end_ms)
        last_lag.append(max(t.recv_offset_ms for t in seg.translation) - end_ms)

    def stat(label: str, values: list[int]) -> None:
        if not values:
            say("- {}: _no data_".format(label))
            return
        say("- {}: median **{} ms**, p90 {} ms, min {}, max {} (n={})".format(
            label,
            round(statistics.median(values)),
            round(sorted(values)[max(0, int(len(values) * 0.9) - 1)]),
            min(values),
            max(values),
            len(values),
        ))

    say("Measured against the real-time send clock "
        "(`recv_offset_ms - end_ms`), so this is end-to-end wall clock.")
    say()
    stat("original final token after the word was spoken", orig_lag)
    stat("first translation token after the segment's last word", first_lag)
    stat("last translation token after the segment's last word", last_lag)
    say()

    # -- sample rendering ---------------------------------------------------
    say("## Sample segments")
    say()
    for seg in segments[:10]:
        say("- **{}** `{}`".format(seg.index, seg.orig_text()))
        say("  - → `{}`".format(seg.tr_text()))
    say()

    return "\n".join(out)


def run_analyze(args: argparse.Namespace) -> int:
    path = Path(args.analyze)
    if not path.exists():
        print("No such capture: {}".format(path), file=sys.stderr)
        return 2
    report = summarize(path)
    print(report)
    if args.report:
        target = Path(args.report)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(report + "\n", encoding="utf-8")
        print("\nwrote {}".format(target), file=sys.stderr)
    return 0


# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", help="audio file to stream (any ffmpeg format)")
    parser.add_argument("--out", default="docs/captures/probe.jsonl",
                        help="where to write the raw response JSONL")
    parser.add_argument("--config", help="JSON file overriding top-level config keys")
    parser.add_argument("--no-glossary", action="store_true",
                        help="send an empty Soniox context")
    parser.add_argument("--analyze", help="analyze an existing capture instead")
    parser.add_argument("--report", help="with --analyze, also write the markdown here")
    args = parser.parse_args()

    if args.analyze:
        return run_analyze(args)
    if not args.file:
        parser.error("one of --file or --analyze is required")
    return asyncio.run(run_stream(args))


if __name__ == "__main__":
    sys.exit(main())
