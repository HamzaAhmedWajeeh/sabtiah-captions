# Phase 1 run sheet

## Setup

**1. ffmpeg on PATH.** The probe shells out to it to decode audio.

    # Windows
    winget install Gyan.FFmpeg
    # macOS
    brew install ffmpeg

Then **open a new terminal** and confirm:

    ffmpeg -version

> **Gotcha, hit during development.** The Gyan.FFmpeg winget package installs
> into a directory with the version in its name
> (`...\Gyan.FFmpeg_...\ffmpeg-9.0.2-full_build\bin`). On upgrade, winget
> rewrites your persisted PATH to the new directory and deletes the old one —
> so **every already-open terminal keeps a PATH pointing at a directory that no
> longer exists**, and ffmpeg vanishes mid-session with no warning. This
> happened here on an 8.1 → 9.0.2 upgrade. The fix is always: open a new
> terminal. The probe detects this and says so rather than raising a
> `FileNotFoundError`.
>
> Worth knowing on run day: a background winget upgrade can break `FileSource`
> rehearsal and the Phase 7 eval tools this way. Live capture does not use
> ffmpeg (that path is `sounddevice`), so a talk in progress is unaffected.

**2. Both inputs present** (check from the project root):

    ls -la .env recordings/podcast-5min.m4a

`.env` must contain a real `SONIOX_API_KEY`. On Windows, confirm the file is
`.env` and not `.env.txt` — Explorer hides known extensions.

**3. The Soniox account has funds.** Checked on 2026-10-10 and it did not: all
three runs below died on `organization_balance_exhausted` (HTTP 402) about
180 ms after the config frame, before a single token. The key was fine — a bad
key gives `unauthenticated`. Add funds or enable autopay in the console, and
set the monthly budget cap while you are there (BUILD_SPEC section 8).

A cheap way to confirm the account is live before committing to 15 minutes of
streaming: run the first command, and if the first line of output is an
`ERROR`, stop. The probe fails in about 2 seconds.

## Run

In order:

    # 1. two-way ar/en, endpoint detection on (BUILD_SPEC section 3 config)
    uv run python tools/probe_soniox.py \
        --file recordings/podcast-5min.m4a \
        --out docs/captures/run1.jsonl

    # 2. one-way to English, the Phase 6 fallback mode
    uv run python tools/probe_soniox.py \
        --file recordings/podcast-5min.m4a \
        --config config/oneway-en.json \
        --out docs/captures/run2-oneway.jsonl

    # 3. two-way again with an empty context, to see what the glossary does
    uv run python tools/probe_soniox.py \
        --file recordings/podcast-5min.m4a \
        --no-glossary \
        --out docs/captures/run3-noglossary.jsonl

    # 4. analyze all three
    uv run python tools/probe_soniox.py --analyze docs/captures/run1.jsonl \
        --report docs/captures/run1.md
    uv run python tools/probe_soniox.py --analyze docs/captures/run2-oneway.jsonl \
        --report docs/captures/run2-oneway.md
    uv run python tools/probe_soniox.py --analyze docs/captures/run3-noglossary.jsonl \
        --report docs/captures/run3-noglossary.md

    # 5. keep run1 and run2 as Phase 2 fixtures (JSONL only, never audio)
    cp docs/captures/run1.jsonl docs/captures/run2-oneway.jsonl tests/fixtures/

`docs/captures/*.jsonl` is gitignored: a capture is a verbatim transcript of
whatever was recorded, so committing one is a deliberate act. Copying into
`tests/fixtures/` is that act — those are tracked normally, and the spec wants
2–3 of them there.

Each pass streams at real time, so budget roughly the recording's duration per
run — about 15 minutes for all three, plus the flush at the end of each.

The recording is a 5-minute cut (10:00–15:00) of a real Arabic podcast, not
read speech, and **there is no reference transcript** — see "The test audio" in
docs/soniox-notes.md for what that does and does not let you conclude. The
audio is from YouTube: `recordings/` is gitignored and **no audio is ever
copied into `tests/fixtures/`**. The JSONL captures are the only artifacts that
get committed.

> **Gotcha, hit on the first run.** Redirecting the probe's stdout to a file on
> Windows used to kill it the instant it printed anything Arabic —
> `UnicodeEncodeError: 'charmap' codec`, because a redirected stdout defaults
> to the locale code page (cp1252) rather than UTF-8. Fixed in the probe
> (`force_utf8_output`), but the same trap waits in any new script in this repo
> that prints captions. On the console, `chcp 65001` is the equivalent fix.

> **Gotcha, same run.** A stream that ends early used to hang the probe for
> minutes and then bury the error under a `RuntimeError: Event loop is closed`
> traceback: asyncio will not reap a subprocess while a pipe transport is still
> open, so terminating ffmpeg with a full stdout pipe blocks `wait()` forever.
> `stop_ffmpeg` now drains before reaping. Worth knowing because `FileSource`
> (Phases 2–3, 5) and the Phase 7 eval tools inherit the same pattern.
