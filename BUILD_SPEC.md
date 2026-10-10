# Sabtiah Live Captions: Build Spec

This is the build plan for Claude Code. Read all of it before writing code. Work phase by phase. Each phase ends with acceptance checks. Do not start the next phase until the current one passes. Where a phase says **STOP and report**, stop, summarize findings for Hamza, and wait.

## 1. What we're building

Live captions for Sabtiah, a weekly AI meetup in Riyadh. Talks are mostly Arabic (Saudi dialect) with English mixed in, sometimes fully English.

- Arabic speech shows English captions. English speech shows Arabic captions.
- Attendees scan a QR code, a web page opens on their phone, captions stream live. No app, no login.
- Optional big-screen view (last 2 lines, large text).
- A laptop at the venue (no GPU) captures audio from the venue mixer and relays it. All speech recognition and translation runs on the Soniox cloud API.

Non-goals for v1: our own ASR model, speech-to-speech, user accounts, a database, a frontend framework, mobile apps.

## 2. Hard constraints

- Python 3.11+, managed with `uv`. Backend: FastAPI + uvicorn. Soniox connection: `websockets` (async). Audio: `sounddevice` + `numpy`. Config: `pydantic-settings` + YAML.
- Frontend: plain HTML + CSS + vanilla JS, served by FastAPI as static files. No build step.
- The Soniox API key lives only on the laptop (`.env`). It never reaches the browser, logs, or git.
- Must run on an ordinary Windows or macOS laptop. Test on both if possible; Windows first (Hamza's dev machine).
- Keep it boring. Prefer fewer files and fewer dependencies.

## 3. Soniox API facts (verified against docs, Oct 2026)

Re-check https://soniox.com/docs/api-reference/stt/websocket-api if anything below fails.

- Endpoint: `wss://stt-rt.soniox.com/transcribe-websocket`
- Auth: header `Authorization: Bearer <SONIOX_API_KEY>` on the connection. Do **not** put `api_key` in the config message (deprecated, refused from 15 Jan 2027).
- First message (text frame): JSON config. Then audio as **binary** frames.
- End stream gracefully: send an **empty text frame** (`""`). Server sends remaining tokens, then `{"finished": true}`, then closes.
- Keepalive: send text frame `{"type": "keepalive"}` at least every 40 s when no audio is flowing, or the connection closes as idle (status 1001). Control types are `"keepalive"` and `"finalize"`.
- Max 300 minutes of audio per stream. After that: error type `max_duration_reached`. Open a new connection.
- Errors arrive as JSON with `error_code`, `error_type`, `error_message`, `request_id`, then the server closes. Branch on `error_type`, never on the message.
- Response: `{"tokens": [...], "final_audio_proc_ms": n, "total_audio_proc_ms": n}`.
- Token fields: `text`, `is_final`, `confidence`, `start_ms`/`end_ms` (absent on translation tokens), `speaker` (if diarization), `language`, `translation_status` (`"none"`, `"original"`, `"translation"`), `source_language`.
- **Final tokens are sent once.** Append them. **Non-final tokens are re-sent in full on every response.** Replace them each time.
- Endpoint detection: with `enable_endpoint_detection: true`, the model finalizes a segment and emits a final token with text `"<end>"`. Never display `<end>`.

Starting config:

```json
{
  "model": "stt-rt-v5",
  "audio_format": "pcm_s16le",
  "sample_rate": 16000,
  "num_channels": 1,
  "language_hints": ["ar", "en"],
  "enable_language_identification": true,
  "enable_speaker_diarization": false,
  "enable_endpoint_detection": true,
  "context": { "general": [], "text": "", "terms": [], "translation_terms": [] },
  "translation": { "type": "two_way", "language_a": "ar", "language_b": "en" }
}
```

Diarization is off by default: endpoint detection lowers its accuracy, and we don't need speaker labels for v1. Endpoint tuning (`endpoint_latency_adjustment_level`, `endpoint_sensitivity`, `max_endpoint_delay_ms`) stays at defaults until Phase 7 data says otherwise.

## 4. Unknowns Phase 1 must answer

We don't know these yet. The caption design depends on them. Do not guess; measure.

1. Do translation tokens for a segment ever arrive **after** that segment's `<end>` token? How often, how late?
2. Is there any end marker on the translation side?
3. For a mixed sentence like "اليوم بنشرح RAG and agentic workflows": what `language` do the original tokens carry? Per token (`en` on "RAG") or per segment? Does the translation keep "RAG" in English?
4. What does `source_language` contain on translation tokens?
5. Typical delay between a word's `end_ms` and its translation token arriving (wall clock).

## 5. Repo layout

```
sabtiah-captions/
  BUILD_SPEC.md
  README.md                 run-day instructions, written last
  pyproject.toml
  .env.example              SONIOX_API_KEY=, ADMIN_TOKEN=, PUBLIC_URL=
  .gitignore                .env, logs/, recordings/, clips/, eval/out/
  config/
    glossary.yaml           terms + translation_terms, shared across sessions
    session.yaml            this Saturday: title, speakers, companies, free text
  app/
    main.py                 FastAPI app, routes, startup/shutdown
    settings.py             env + YAML loading
    audio_source.py         MicSource, FileSource (same interface)
    soniox_client.py        connection, send loop, receive loop, reconnect
    captions.py             token assembly into caption state (pure, no I/O)
    broadcast.py            viewer WebSocket hub
    session_log.py          JSONL logger
  web/
    index.html              audience page (phones)
    screen.html             big-screen view
    admin.html              operator page
    app.js, screen.js, admin.js, style.css
  tools/
    probe_soniox.py         Phase 1
    cut_clips.py            Phase 7
    eval_run.py             Phase 7
    wer.py                  Phase 7
  tests/
    fixtures/               raw Soniox JSON captured in Phase 1
    test_captions.py
  docs/
    soniox-notes.md         Phase 1 findings
  logs/  recordings/  clips/  (gitignored)
```

## 6. Architecture

```
Mixer line-out -> USB audio interface -> MicSource (16 kHz mono int16, 100 ms chunks)
                                             |
                                       audio queue (asyncio)
                                             |
                                    SonioxClient --WSS--> Soniox stt-rt-v5
                                             |   <-- tokens (transcript + translation)
                                    CaptionState (captions.py)
                                             |
                                 BroadcastHub (throttled ~10 Hz)
                                             |
                       FastAPI /ws  <-- Cloudflare named tunnel <-- phones, screen
```

One process, one asyncio event loop. Audio capture callback runs in sounddevice's thread and hands chunks to the loop with `loop.call_soon_threadsafe(queue.put_nowait, chunk)`.

## 7. Phases

### Phase 0: Skeleton

- Create the layout above, `pyproject.toml`, `.env.example`, `.gitignore`.
- `app/main.py` serves `web/index.html` at `/` and a `/healthz` returning `{"ok": true}`.
- `uv run uvicorn app.main:app --reload` works.

Acceptance: page loads at localhost, `/healthz` returns ok, `.env` is gitignored.

### Phase 1: Probe Soniox (answer section 4)

`tools/probe_soniox.py`:

- Args: `--file <audio>` (any format; decode with ffmpeg to 16 kHz mono s16le), `--config <json>` (optional override), `--out <path.jsonl>`.
- Streams the file in 100 ms chunks paced at real time (sleep between chunks), using the config in section 3.
- Writes every raw response to JSONL with a wall-clock timestamp `recv_at` and the audio offset sent so far `sent_ms`.
- Sends `""` at end, waits for `finished`.
- Prints a readable rendering: final text solid, non-final in brackets, translation lines prefixed `[TR <lang>]`.

Inputs: Hamza will provide a recording from a Sabtiah session. If none is available yet, ask him to record himself saying 10 sentences: 3 Saudi Arabic, 3 English, 4 mixed (Arabic with English tech terms). Include "اليوم بنشرح RAG and agentic workflows".

Then write `docs/soniox-notes.md` answering the 5 questions in section 4 with evidence (quote token excerpts from the JSONL). Copy 2 or 3 representative JSONL captures into `tests/fixtures/`.

**STOP and report** to Hamza: the answers, 5 example outputs (good and bad), and a recommendation on caption design (Phase 2 option A or B).

### Phase 2: Caption state (pure logic, fully tested)

`app/captions.py`. No network, no async. Input: Soniox response dicts. Output: a serializable snapshot.

Core rules for any design:

- Keep final tokens; replace the non-final buffer on every response.
- Strip `<end>`; treat it as a segment boundary for the original stream.
- Group text by **output language** (`ar` / `en`), not by original vs translation. The English view is "everything in English": English speech plus translations of Arabic speech. Same for Arabic.
- Cap memory: keep the last 200 segments.

**Option B (default, safe regardless of Phase 1 results):** two rolling streams, `en` and `ar`. Each stream = list of paragraphs, each paragraph has `final` text and a trailing `partial`. Original-stream `<end>` starts a new paragraph in the original's language stream. For the translated stream, count pending endpoints and break a paragraph when the first translation token after an endpoint arrives. Mixed-language original tokens: assign the segment to its majority language so English words inside an Arabic sentence don't leak into the English stream as fragments (confirm with Phase 1 notes).

**Option A (only if Phase 1 shows translations reliably finish before or right at `<end>`):** line-aligned segments, each with `orig` and `translation`, so the "both" view can show a source line and its translation together.

Snapshot shape (Option B):

```json
{
  "seq": 1042,
  "live": true,
  "streams": {
    "en": [{"id": 311, "final": "Today we'll explain RAG", "partial": " and agentic"}],
    "ar": [{"id": 311, "final": "اليوم بنشرح RAG", "partial": " and agentic"}]
  }
}
```

Broadcast **diffs** after the first snapshot: changed paragraphs only, keyed by `id`, plus `seq`. A client that sees a `seq` gap requests a full snapshot.

Tests (`tests/test_captions.py`), replaying `tests/fixtures/*.jsonl`:

- non-final replacement never duplicates text
- `<end>` never appears in output
- English view contains no Arabic script; Arabic view contains no Latin except allowed technical terms (check against glossary)
- paragraph count is stable when the same responses are replayed twice
- 200-segment cap holds

Acceptance: `uv run pytest` green. Rendering a fixture to text reads sensibly.

### Phase 3: Server, broadcast, audience page

Backend (`app/main.py`, `app/broadcast.py`):

- `GET /` audience page, `GET /screen` big-screen view, `GET /admin` operator page.
- `WS /ws` viewers: on connect send full snapshot, then diffs. Server sends a ping every 20 s. Read-only: ignore any client message except `{"type":"resync"}`.
- Throttle broadcasts to at most 10 per second; coalesce diffs in between.
- Limit to 500 concurrent viewer sockets; drop the slowest if its send buffer backs up (don't let one slow phone stall everyone).
- A `FileSource` path (from Phase 1) can drive the whole pipeline, so the page can be built against recorded audio.

Audience page (`web/index.html`, `app.js`, `style.css`):

- Top bar: "Sabtiah Live Captions", view toggle `English | العربية | Both`, and a small line: "Live auto-translation. May contain mistakes." / "ترجمة آلية مباشرة، قد تحتوي على أخطاء."
- Default view: English if the browser language isn't Arabic, else Arabic. Remember choice in localStorage (wrap in try/catch).
- Dark background, large text (base 22px, user-adjustable A-/A+), high contrast.
- Arabic paragraphs `dir="rtl"`, English `dir="ltr"`; use `dir="auto"` on mixed content. Arabic font: IBM Plex Sans Arabic or Noto Naskh Arabic from Google Fonts with system fallback.
- Final text in full color, partial text dimmed (60% opacity). No flicker: update partial span text in place, don't re-render the list.
- Auto-scroll to newest. If the user scrolls up, stop auto-scroll and show a "Jump to live" button.
- States: connecting, live, "No session live right now. Captions start when the talk does." (server says `live:false`), reconnecting (exponential backoff 1s to 15s, then resync).
- Works on iOS Safari and Android Chrome. No horizontal scroll at 360px width.

Screen view (`/screen?lang=en&lines=2`): black background, last N paragraphs only, huge text, no controls. Designed to be overlaid under slides.

Acceptance: replaying a recording through FileSource shows captions on 3 phones on the same Wi-Fi at the same time; toggles work; scroll-back and Jump to live work; killing and restarting the server makes pages reconnect and resync on their own.

### Phase 4: Live audio capture

`app/audio_source.py`:

- `MicSource(device, sample_rate=16000)`: sounddevice `RawInputStream`, mono, int16, 100 ms blocks. If the device can't do 16 kHz, capture at its native rate and resample with numpy (polyphase or simple linear is fine for speech). Pick channel 1 if the interface is stereo; allow `--channel`.
- `FileSource(path, realtime=True)`: same interface, used for rehearsal and tests.
- `tools/list_devices.py` prints input devices with index, name, channels, default sample rate.
- Compute RMS level per chunk and expose it (for the admin meter).
- Optional `--record` writes the raw captured audio to `recordings/<date>.wav` (only when Hamza turns it on; off by default for privacy).

Acceptance: captions from a live mic on the laptop show on a phone; admin meter moves with speech; clipping (peaks at max) is visible on the meter.

### Phase 5: Resilience

This is what makes or breaks the live demo. `app/soniox_client.py`:

- Connection states: `idle`, `connecting`, `streaming`, `reconnecting`, `stopped`. Expose to admin.
- Two tasks per connection: sender (audio queue -> binary frames) and receiver (responses -> CaptionState).
- While capture is paused but the session is live, send `{"type":"keepalive"}` every 20 s.
- On disconnect or error: classify by `error_type`.
  - retryable (`service_unavailable`, `internal_error`, `request_timeout`, network errors, `max_duration_reached`): reconnect with backoff 0.5s, 1s, 2s, 4s, cap 8s.
  - fatal (`unauthenticated`, `permission_denied`, `organization_balance_exhausted`, `*_budget_exhausted`, `invalid_request`): stop, show the error on admin, don't loop.
- Audio ring buffer: keep the last 20 s of captured audio. On reconnect, send the audio captured since the last `final_audio_proc_ms` (capped at 20 s) before resuming live audio, so a short drop loses nothing.
- Proactive rollover at 280 minutes of streamed audio: open a new connection, start sending, then close the old one gracefully with `""`.
- On reconnect, finalize any open partial text in CaptionState (mark it final as-is) so the page doesn't show a stuck grey line.
- Mark the stream `live:false` only when the operator stops it, not during reconnects; during reconnect send `{"status":"reconnecting"}` so pages can show a small indicator.

Acceptance (do these by hand and write results in `docs/resilience-test.md`):

1. Turn off Wi-Fi for 10 s mid-sentence, turn it back on: captions resume, the sentence spoken during the drop appears.
2. Kill the network for 2 minutes: admin shows reconnecting, pages show indicator, recovery is automatic.
3. Invalid API key: admin shows a clear error, no retry loop.
4. Run 60 minutes continuously from FileSource with no memory growth beyond the 200-segment cap.

### Phase 6: Operator page, context and glossary

`/admin?token=<ADMIN_TOKEN>` (constant-time compare; 401 otherwise; never link it from the audience page):

- Start / Stop session. Input device dropdown. Source: mic or file.
- Status: Soniox state, last error, audio level meter, viewer count, seconds since last final token, estimated delay (wall clock now minus the wall-clock time the audio at `final_audio_proc_ms` was sent).
- Session context editor: talk title, speaker names, companies, free text. Saved to `config/session.yaml`; applied on next Start (Soniox context is set per connection).
- Toggles: logging on/off, recording on/off, translation direction (two-way / Arabic to English only / English to Arabic only). One-way uses `{"type":"one_way","target_language":"en"}`; this is the fallback if two-way is weak on mixed speech.
- A "Clear captions" button (new speaker, fresh page).

`config/glossary.yaml`:

```yaml
terms: [RAG, MCP, LLM, LLMs, fine-tuning, agent, agents, agentic, embeddings, vector database, Sabtiah, Cycls]
translation_terms:
  - {source: agent, target: agent}
  - {source: RAG, target: RAG}
  - {source: Sabtiah, target: السبتية}
```

Build the Soniox `context` from glossary + session: `general` = title/speakers/companies as key-value items, `text` = free text, `terms`, `translation_terms`. Keep total context well under Soniox's 8000-token limit; warn on admin if over 6000 (rough estimate: chars / 3).

`app/session_log.py`: when logging is on, append one JSON line per finalized paragraph to `logs/<YYYY-MM-DD>.jsonl`: `{ts, id, lang, text, kind: "original"|"translation", source_language}`. Never log audio unless recording is on. Logs are never served over HTTP.

Acceptance: changing a speaker name in admin, restarting the session, and saying the name results in correct spelling in captions.

### Phase 7: Evaluation harness

For comparing engines and tuning settings. Reuse patterns from Hamza's ELM ASR benchmark if he shares it.

- `tools/cut_clips.py --in recordings/x.wav --segments segments.csv`: cuts clips with ffmpeg; CSV columns `clip_id,start,end,category` with category in `saudi|english|mixed`.
- References: `clips/<id>.ref.txt` written or corrected by Hamza.
- `tools/eval_run.py --engine soniox [--engine speechmatics] --clips clips/`: streams each clip in real time, saves transcript, translation, time-to-first-token, time-to-final.
- `tools/wer.py`: WER/CER with `jiwer`. Arabic normalization before scoring: remove diacritics and tatweel, unify alef forms (أ إ آ -> ا), ى -> ي, ة -> ه, strip punctuation, lowercase Latin. Report per category.
- Output `eval/out/report.csv` and a short `eval/out/summary.md`.
- Speechmatics adapter (Plan B) only if Hamza asks after seeing Soniox numbers: real-time API, Arabic-English bilingual language pack `ar_en`, transcript only; translation then done by an LLM on finalized segments with the last 3 segments and glossary as context. Keep it behind the same interface so CaptionState doesn't change.

Acceptance: one command produces the report for all clips.

### Phase 8: Public link and run-day kit

- Cloudflare **named** tunnel to a fixed hostname (e.g. `captions.<domain>`), so the QR code never changes. Quick tunnels (`trycloudflare.com`) give a new URL each run: use only for testing.
- `docs/tunnel.md`: exact `cloudflared` install, login, `tunnel create`, DNS route, `config.yml` pointing to `http://localhost:8000`, and how to run it. Confirm WebSockets pass through.
- `tools/make_qr.py`: generates a print-ready PNG/PDF of the QR (with the URL printed under it and "Live captions / ترجمة مباشرة").
- `scripts/start.ps1` and `scripts/start.sh`: start uvicorn and cloudflared together, open admin in the browser.
- `README.md` run-day checklist:
  1. Laptop charged, charger, USB audio interface, XLR + 6.35 mm cables, 3.5 mm adapter, ground loop isolator, 5G hotspot.
  2. Plug into mixer AUX/REC/line out; check level meter while the speaker talks; adjust gain so peaks stay below clipping.
  3. Laptop on hotspot, not venue Wi-Fi.
  4. Fill session context in admin, Start.
  5. Scan the QR with your own phone, confirm captions.
  6. Watch admin during the talk: delay, errors, viewers.
- `docs/rehearsal.md`: replay a full recorded session through FileSource with 3 phones connected; record delay numbers and any glitches.

Acceptance: from a phone on mobile data (not the venue/home Wi-Fi), scanning the printed QR opens the live page and shows captions from the laptop.

## 8. Security and privacy checklist

- API key only in `.env`; never sent to clients, never logged. Add a test that greps served static files for `SONIOX`.
- Admin behind a token; audience endpoints are read-only.
- Logs and recordings never served. Both off unless turned on in admin.
- Don't record or log a talk whose speaker said no. Captions without logging is always allowed.
- Set a monthly budget cap in the Soniox console (budget-exhausted errors are handled as fatal).

## 9. Working rules for Claude Code

- Ask before adding a dependency not listed in section 2.
- Commit at the end of every phase with a message naming the phase.
- When the Soniox API behaves differently from section 3, trust the live behavior, update `docs/soniox-notes.md`, and tell Hamza.
- Prefer small pure functions; CaptionState stays free of I/O so it can be tested with fixtures.
- Don't build features not in this spec. Put ideas in `docs/later.md`.

## 10. Timeline

Target: demo at Sabtiah on Saturday Oct 17, 2026.

| Day | Phases |
| --- | --- |
| Sun Oct 11 | 0, 1 (STOP and report) |
| Mon Oct 12 | 2, start 7 (clip scoring) |
| Tue Oct 13 | 3, 4 |
| Wed Oct 14 | 5, 6 |
| Thu Oct 15 | 8, full rehearsal |
| Fri Oct 16 | Fixes from rehearsal only. No new features. |
