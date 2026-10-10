# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Status

The repo currently contains only `BUILD_SPEC.md` — no code, no git repo yet. Work starts at Phase 0 of the spec (skeleton + `pyproject.toml` + `.gitignore`). Initialize git before the first commit.

## BUILD_SPEC.md is the contract

`BUILD_SPEC.md` is the authoritative plan, written by Hamza (the operator/owner). Read it in full before writing code. It defines the repo layout, architecture, 9 phases with acceptance checks, and explicit non-goals.

Rules it imposes that override normal defaults:

- **Work phase by phase.** Do not start a phase until the previous one's acceptance checks pass. Phases marked **STOP and report** (Phase 1) end the turn — summarize findings and wait for Hamza.
- **Ask before adding any dependency** not listed in spec section 2.
- **Don't build features not in the spec.** Ideas go in `docs/later.md`.
- Commit at the end of every phase, with the phase named in the message.
- When live Soniox behavior contradicts spec section 3, trust the live behavior, update `docs/soniox-notes.md`, and tell Hamza.

## Commands

Python 3.11+ via `uv`. These are the intended commands (they exist once the matching phase lands):

```bash
uv sync                                          # install deps
uv run uvicorn app.main:app --reload             # dev server, port 8000
uv run pytest                                    # all tests
uv run pytest tests/test_captions.py::test_name  # single test
uv run python tools/list_devices.py              # audio input devices (Phase 4)
uv run python tools/probe_soniox.py --file <audio> --out <path.jsonl>   # Phase 1
uv run python tools/eval_run.py --engine soniox --clips clips/          # Phase 7
```

Windows is the primary dev machine; `scripts/start.ps1` / `scripts/start.sh` (Phase 8) launch uvicorn + cloudflared together.

## Architecture

One process, one asyncio event loop, no database, no frontend framework, no build step.

```
mixer line-out -> USB interface -> audio_source.py (16 kHz mono int16, 100 ms chunks)
                                        -> asyncio queue
                                        -> soniox_client.py --WSS--> Soniox stt-rt-v5
                                        -> captions.py (CaptionState)
                                        -> broadcast.py (throttled ~10 Hz)
                                        -> FastAPI /ws -> Cloudflare named tunnel -> phones
```

Key design seams:

- **`captions.py` is pure.** No network, no async, no I/O. Takes Soniox response dicts, returns a serializable snapshot. This is what makes fixture-replay testing possible — keep it that way.
- **`AudioSource` has two implementations** (`MicSource`, `FileSource`) behind one interface. `FileSource` drives the entire pipeline from a recording, so Phases 2–3 and 5 can be built and tested without a mixer or live speech.
- **Engine adapters sit behind one interface** (`soniox_client.py`, optionally a Speechmatics adapter in Phase 7) so `CaptionState` never changes when the engine does.
- **Captions are grouped by output language, not by original vs. translation.** The `en` stream is "everything in English" — English speech plus translations of Arabic speech. Same for `ar`. Arabic speech shows English captions and vice versa; that inversion is the product.
- **Broadcast sends a full snapshot on connect, then diffs** keyed by paragraph `id` with a monotonic `seq`. Clients that see a `seq` gap send `{"type":"resync"}`; the server ignores every other client message.
- The audio capture callback runs in sounddevice's own thread and must hand chunks over with `loop.call_soon_threadsafe(queue.put_nowait, chunk)`.

## Soniox gotchas that cause real bugs

Full details in spec section 3; these are the ones that silently corrupt output:

- **Final tokens are sent once — append them. Non-final tokens are re-sent in full every response — replace them.** Appending non-finals duplicates text.
- Endpoint detection emits a final token with text `"<end>"`. It is a segment boundary, never display it.
- Auth is the `Authorization: Bearer` **header**; `api_key` in the config message is deprecated and refused from 15 Jan 2027.
- Config is the first **text** frame; audio frames are **binary**. End gracefully with an empty text frame (`""`), then wait for `{"finished": true}`.
- Send `{"type":"keepalive"}` at least every 40 s when no audio flows (spec uses 20 s), or the server drops the connection as idle.
- 300-minute cap per stream; the spec rolls over proactively at 280 min.
- Branch on `error_type`, never on `error_message`. Retryable vs. fatal classification is in Phase 5 and determines whether to back off or stop — a retry loop on `unauthenticated` is a bug.

## Soniox budget: $10 total, and that is the whole project

$10 of Soniox credit has to cover development, testing, rehearsal **and** the
live demo on Saturday Oct 17, 2026. Every streamed second is spent money, so
treat the API as the scarce resource it is.

- **Never send audio to Soniox unless the task genuinely needs live API
  behavior.** Caption logic, UI, broadcast, diffing and reconnect work all run
  offline by replaying saved JSONL captures (`tests/fixtures/`,
  `docs/captures/`) through a fake Soniox client. Build that replay mode in
  Phase 2 or 3 so the full pipeline runs with no network at all.
- **Default test clips are 1-5 minutes.** Ask Hamza before any run longer than
  10 minutes of audio, and before re-running a capture that already exists -
  re-running a capture we have is spending money to learn nothing.
- **The stream must stop when the session stops**, and must never sit streaming
  silence. Add an idle auto-stop: if audio stays below a silence threshold for
  10 minutes, end the Soniox stream and show "paused" on `/admin`.
- **Log audio seconds sent per run to `logs/usage.jsonl`**, and show the running
  total - minutes and estimated $ - on the admin page.

## Security invariants

- `SONIOX_API_KEY` lives only in `.env` on the laptop. Never in the browser, logs, or git. There is a test that greps served static files for `SONIOX`.
- `/admin` requires `ADMIN_TOKEN` with a constant-time compare, and is never linked from the audience page. Audience endpoints are read-only.
- `logs/`, `recordings/`, `clips/` are gitignored and never served over HTTP. Logging and recording are both off by default — privacy consent matters; captions without logging is always allowed.

## Hard deadline

Live demo at the Sabtiah meetup on Saturday Oct 17, 2026. Spec section 10 has the day-by-day plan. Friday Oct 16 is fixes only, no new features.
