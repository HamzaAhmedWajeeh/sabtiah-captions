# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Status

**Phases 0–5 are done, and Phase 6's session context is done early. The rest of Phase 6 (logging toggles, translation direction, clear-captions) is next.**

**Four live runs have happened** (run6-run9 on 2026-10-13; write-ups in `docs/live/`). Acceptance checks 1 and 2 in `docs/resilience-test.md` now pass live; 3 and 4 are still unrun. Live runs up to 60 minutes no longer need asking — see the budget section.

- **Phase 1 is answered**, with five captures in `docs/captures/` and the findings in `docs/soniox-notes.md`. Read that file before touching caption logic — several of its measurements contradict assumptions in BUILD_SPEC section 3 and section 6.
- **Phase 2** is `app/captions.py` (pure, Option B) plus `app/replay.py`, the fake Soniox client. It paces a capture at real time, so the pages can be judged at true speed.
- **Phase 3** is `app/broadcast.py` (viewer hub), `app/main.py` (routes, `/ws`, the replay session) and the three pages in `web/`.
- **Phase 4** is `app/audio_source.py` (`MicSource`, `FileSource`), `tools/list_devices.py`, the admin level meter and `app/usage.py` (the `logs/usage.jsonl` spend ledger).
- **The audience page recovers on its own.** The server heartbeats the viewer socket every 5 s and the page reconnects after 12 s of silence, plus on `online` and on becoming visible. run9 found the phone frozen on a dead socket that never fired `onclose` - a dead socket is indistinguishable from a quiet one, so silence is the only signal there is. Do not raise `PING_INTERVAL` without lowering the page's `SILENCE_LIMIT` with it; a test asserts the relationship.
- **Stop flushes before it closes.** `SonioxClient.finish()` sends the end-of-audio frame and waits up to 2 s for the last tokens. Cancelling the tasks outright loses the end of the talk, which is how run9 ended "this is Hamza sig".
- **Phase 5** is `app/soniox_client.py`: ring buffer, backoff, error classification, keepalive, rollover, idle auto-stop. It takes its `connect` as an argument and imports `websockets` lazily, so no test can reach Soniox even by accident. Keep it that way.
- **Session context (Phase 6, early)** is editable from `/admin` at runtime: save writes `config/session.yaml` atomically, and "Apply now" reconnects through the rollover path so a mid-talk change costs no audio and shows the audience nothing. **469 tests, all offline.**
- **`docs/CHECKPOINT.md` is the short version**: what is done, what is next, the open items, and Soniox audio spent (**31.2 minutes** so far, about 9 cents of the $10 — see the budget section).
- Two findings matter most for the phases ahead. **Endpoint detection can effectively never fire** — `run4-saudi` gave one `<end>` in 300 s — so `<end>` is not a reliable paragraph break and `captions.py` adds a soft size break. And **nothing stays in Latin script in Arabic captions**: `RAG`/`MCP`/`LLM` get transliterated in the `en`→`ar` direction. The `translation_terms` fix in `config/glossary.yaml` is untested and is the highest-value remaining experiment.

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
uv run python tools/probe_soniox.py --file <audio> --out <path.jsonl>   # streams audio, ~$0.003/min
uv run python tools/eval_run.py --engine soniox --clips clips/          # streams audio, ~$0.003/min

# offline: replay a capture through CaptionState, no Soniox calls
uv run python tools/render_capture.py tests/fixtures/run1.jsonl --stats
uv run python tools/render_capture.py tests/fixtures/run5-english.jsonl --lang ar
uv run python tools/probe_soniox.py --analyze docs/captures/run1.jsonl
```

Replay drives the whole server offline. With uvicorn running, start a session
from the operator page (`/admin?token=<ADMIN_TOKEN>`) or directly:

```bash
curl -X POST "http://127.0.0.1:8000/admin/start?token=<ADMIN_TOKEN>&capture=run1.jsonl&paced=true"
curl -X POST "http://127.0.0.1:8000/admin/stop?token=<ADMIN_TOKEN>"
```

`paced=true` replays at real time (judge the page at true speed, caption lag
included); `paced=false` pushes a whole capture through in under a second,
which is the quick way to fill the page or stress the broadcast throttle.

Only `probe_soniox.py --file`, `eval_run.py` and a live session from `/admin` reach Soniox at all. Everything else, including the whole test suite, runs off saved captures. At $0.003 a minute none of it is expensive; the reason to prefer replay is speed and determinism, not cost.

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
- **A graceful end and an error both end the stream, and they are told apart by *which task finished*, not by the message.** When the client sends the empty text frame on purpose (rollover, idle pause), the server answers `{"finished": true}` and both the sender and receiver complete. Ask the sender why it stopped first; reading the receiver first turns a rollover into "the talk ended". See `app/soniox_client._one_connection`.
- Endpoint detection emits a final token with text `"<end>"`. It is a segment boundary, never display it.
- Auth is the `Authorization: Bearer` **header**; `api_key` in the config message is deprecated and refused from 15 Jan 2027.
- Config is the first **text** frame; audio frames are **binary**. End gracefully with an empty text frame (`""`), then wait for `{"finished": true}`.
- Send `{"type":"keepalive"}` at least every 40 s when no audio flows (spec uses 20 s), or the server drops the connection as idle.
- 300-minute cap per stream; the spec rolls over proactively at 280 min.
- Branch on `error_type`, never on `error_message`. Retryable vs. fatal classification is in Phase 5 and determines whether to back off or stop — a retry loop on `unauthenticated` is a bug.

## Soniox budget: $10, and it is not the constraint it looked like

**Rate checked on soniox.com/pricing, 2026-10-13: about $0.18 per hour** with
two-way translation — $2 per million audio tokens and $4 per million text
tokens, at roughly 30k of each per hour. That is **$0.003 a minute**, so the
$10 covers about **55 hours** of streaming.

For scale: everything this project has streamed to date is 31.2 minutes, which
cost **about 9 cents**. The earlier rules in this file were written before the
rate was known and assumed the budget was tight. It is not.

- **Replay is still the default for development.** Caption logic, UI,
  broadcast, diffing and reconnect work all run offline by replaying saved
  JSONL captures (`tests/fixtures/`, `docs/captures/`) through
  `replay.FakeSonioxClient`. That is not about money any more — it is about
  tests that are fast, deterministic and runnable on a train. A test that
  needs the network is a worse test.
- **Live runs up to 60 minutes are fine without asking.** An hour costs $0.18,
  1.8% of the budget. Ask before anything longer, and still ask before
  re-running a capture that already exists — not because of the cost, but
  because it spends time to learn nothing.
- **The stream must stop when the session stops**, and must never sit
  streaming silence. The idle auto-stop stays: ten minutes below the silence
  threshold ends the stream and shows "paused" on `/admin`. It is cheap
  insurance against a session left open overnight, which is the one way left
  to spend real money by accident.
- **Keep logging audio seconds per run to `logs/usage.jsonl`**, and keep the
  running total on the admin page. The number to watch is no longer "are we
  about to run out" but "did something stay connected when nobody was
  talking".
- `SONIOX_RATE_PER_MINUTE=0.003` in `.env` turns the dollar figures on.

## Security invariants

- **Session details are private.** Speaker names, the talk title, companies and the operator's notes live in `config/session.yaml`, which is **gitignored** — this repo is public. They may reach only the Soniox `context` object and `/admin` behind its token. Never the audience pages, the viewer socket, `/admin/status`, `logs/usage.jsonl`, a response log, a capture, or an error message; those carry counts at most (`SessionContext.summary()`). `tests/test_session_privacy.py` enforces this with values that cannot occur by accident — it already caught one leak, the Soniox config being written verbatim into the response log's meta line.
- `SONIOX_API_KEY` lives only in `.env` on the laptop. Never in the browser, logs, or git. There is a test that greps served static files for `SONIOX`.
- `/admin` requires `ADMIN_TOKEN` with a constant-time compare, and is never linked from the audience page. Audience endpoints are read-only.
- **The admin token comes from `.env` in the repo root, and from nowhere else.** Never start the server with a token passed on the command line or in a temporary environment variable, not even for a quick browser check. A throwaway value means the real token goes untested: the session looks fine all afternoon and then fails when it is started the way the README says. `.env` is read from the repo root whatever directory the server is started from, and the server **refuses to start** if the token is missing or empty rather than serving pages with `/admin` permanently 401.
- **Never print, echo, log or commit the real token value** - not in chat, not in a URL in a command, not in test output, not in a commit message. Say `?token=<ADMIN_TOKEN>` and let the reader fill it in. Tests use their own obvious fakes, and `tests/test_server.py` checks that the operator's real token appears in no committed file.
- `logs/`, `recordings/`, `clips/` are gitignored and never served over HTTP. Logging and recording are both off by default — privacy consent matters; captions without logging is always allowed.

## Deadlines

**Changed on 2026-10-13.** Saturday Oct 17, 2026 is now a **conversation with the Sabtiah organizer**, not a live demo: what it needs is a working demo on Hamza's phone, shown to one person. **The first real session is about a week later, roughly Sat Oct 24.**

Spec section 10's day-by-day plan was written for the old date and is no longer the schedule. Everything else in BUILD_SPEC still holds.
