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

    ls -la .env recordings/test-sentences.m4a

`.env` must contain a real `SONIOX_API_KEY`. On Windows, confirm the file is
`.env` and not `.env.txt` — Explorer hides known extensions.

## Run

In order:

    # 1. two-way ar/en, endpoint detection on (BUILD_SPEC section 3 config)
    uv run python tools/probe_soniox.py \
        --file recordings/test-sentences.m4a \
        --out docs/captures/run1.jsonl

    # 2. one-way to English, the Phase 6 fallback mode
    uv run python tools/probe_soniox.py \
        --file recordings/test-sentences.m4a \
        --config config/oneway-en.json \
        --out docs/captures/run2-oneway.jsonl

    # 3. analyze both
    uv run python tools/probe_soniox.py --analyze docs/captures/run1.jsonl \
        --report docs/captures/run1.md
    uv run python tools/probe_soniox.py --analyze docs/captures/run2-oneway.jsonl \
        --report docs/captures/run2-oneway.md

    # 4. keep them as Phase 2 fixtures
    cp docs/captures/run1.jsonl docs/captures/run2-oneway.jsonl tests/fixtures/

`docs/captures/*.jsonl` is gitignored: a capture is a verbatim transcript of
whatever was recorded, so committing one is a deliberate act. Copying into
`tests/fixtures/` is that act — those are tracked normally, and the spec wants
2–3 of them there.

The recording is 10 scripted sentences with 1-2 s pauses: 1-3 Saudi Arabic,
4-6 English, 7-10 mixed Arabic with English tech terms. The script is in
docs/soniox-notes.md for output comparison. Each pass streams at real time, so
budget roughly the recording's duration per run.
