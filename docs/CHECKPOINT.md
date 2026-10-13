# Checkpoint — 2026-10-13 (end of day, plus session context)

**The plan changed.** Saturday Oct 17 is a **conversation with the Sabtiah
organizer**, not a live demo. What it needs is a working demo on Hamza's
phone, shown to one person. The first real session is about a week later,
roughly Sat Oct 24.

That moves the deadline that mattered. Nothing is due on a stage this week,
so Phase 5 resilience and the Phase 8 tunnel have a week more than they had
this morning — and the thing to protect now is a phone that shows captions
convincingly over Wi-Fi, which it does today off a replay.

**Two live runs happened, and they settled the biggest open question.**

- **run6** (1.36 min): the pipeline works end to end — headset, real Wi-Fi,
  captions on the phone in both languages, no errors.
- **run7** (2.88 min): **`translation_terms` works.** `RAG`, `embeddings`,
  `LLM`, `MCP`, `fine-tuning` and `transformers` all stayed in English in the
  Arabic captions. That question had been open since 2026-10-11 and is now
  answered in `docs/soniox-notes.md`.

run7 also broke, usefully: a deliberate 10 s Wi-Fi drop became a **32.5 s
outage** and lost ~12.5 s of speech.

- **run8** (1.73 min) confirmed the fixes on the phone: `[connection
  interrupted]` in both streams, captions back **live** with no lag, no glued
  words, and the stale error cleared. BUILD_SPEC acceptance 1 and 2 now pass
  live. It also produced the project's first raw token-level log.

**Live testing is paused for the week.** Phases 0–5 done, and Phase 6's
session context landed early because the details turned out to be **private
and runtime-editable** — see below. The rest of Phase 6 (logging toggles,
translation direction, clear captions) is next.

## Done

- **Phase 0** — skeleton, `pyproject.toml`, `.gitignore`, FastAPI stub.
- **Phase 1** — all five section 4 questions answered from five live captures.
  Findings in `docs/soniox-notes.md`; **read it before touching caption
  logic.**
- **Phase 2** — `app/captions.py` (pure, Option B, two per-language streams)
  and `app/replay.py` (`FakeSonioxClient` over saved JSONL).
- **Phase 3** — `app/broadcast.py`, `app/main.py`, and the three pages in
  `web/`. Driven entirely by `FakeSonioxClient`.
- **Phase 4** — `app/audio_source.py` (`MicSource`, `FileSource`),
  `tools/list_devices.py`, the admin level meter, `--record`, and
  `app/usage.py` writing `logs/usage.jsonl`.
- **Phase 5** — `app/soniox_client.py`: the real connection, with keepalive,
  ring buffer, reconnect with backoff, error classification, rollover and the
  idle auto-stop. Results and the outstanding live checks are in
  **`docs/resilience-test.md`**.

**279 tests green, all offline** — 145 from Phases 2–3, 61 for Phase 4, 73 for
Phase 5 and the caption fix. No test touches the network: `SonioxClient` takes
its `connect` as an argument and imports `websockets` lazily, so no test can
dial Soniox even by accident.

## Soniox spent: 27.7 minutes, about 8 cents

22.0 from Phase 1, **1.36 from run6**, **2.88 from run7**, **1.48 from run8**.
The ledger in `logs/usage.jsonl` is the record; `/admin` shows the running
total.

**The rate is now known** — soniox.com/pricing, checked 2026-10-13: about
**$0.18 per hour** with two-way translation ($2 per million audio tokens,
$4 per million text tokens, roughly 30k of each per hour). That is **$0.003 a
minute**, so the $10 covers about **55 hours**.

Everything streamed to date therefore cost **about 8 cents, 0.8% of the
budget**. The project has been run all week as though credit were the binding
constraint; it never was. The rules in CLAUDE.md are updated:

- Replay stays the default for development, for speed and determinism rather
  than cost.
- **Live runs up to 60 minutes no longer need asking** — an hour is $0.18.
- The idle auto-stop and the usage ledger both stay. A session left connected
  overnight is now the only realistic way to spend real money by accident,
  which is exactly what the auto-stop is for.

Set `SONIOX_RATE_PER_MINUTE=0.003` in `.env` and `/admin` shows dollars.

## The two live runs

**run6** — `Microsoft Sound Mapper` (device 0) at native 16 kHz, 221
responses, no errors, no reconnects. Confirmed the whole path works. Its
transcript had to be recovered from the server's memory after the fact, which
is why `tools/dump_session.py` exists: **a live run writes no transcript of its
own** until `session_log.py` lands in Phase 6.

**run7** — 184 s, 522 responses, 7 reconnect attempts across one outage. The
glossary result is in `docs/soniox-notes.md`; the outage is in
`docs/resilience-test.md`. Recorded live by `tools/watch_run.py`, which is how
the outage could be measured to the half-second afterwards.

Two terms did **not** obey `translation_terms`: `agents` became أجهزة
("devices") and `AI` became الذكاء الاصطناعي. Singular-versus-plural is not the
explanation — both forms were in the list, and `transformers`/`embeddings` are
plural and survived. `AI` has been removed from `translation_terms` on purpose
(the Arabic is more readable); `agents` is left in to be retested.

Phase 4 cost nothing and, like Phase 3, **structurally cannot**: nothing in
`audio_source.py` imports `websockets` or opens a socket. Capturing audio is
free; only Phase 5's client makes it cost anything.

The figure now lives in `logs/usage.jsonl` rather than only in this file.
It was seeded from the five Phase 1 captures themselves — each capture's last
`sent_ms` is the audio that was streamed to produce it — so the 22.0 minutes
is derived, not asserted. `/admin` shows the running total.

## Phase 4: what was built

`app/audio_source.py` — one interface, two implementations, both emitting
**16 kHz mono int16 in exact 100 ms chunks**, which is what Soniox is fed:

- **`MicSource`** — `sounddevice.RawInputStream`. The callback runs on
  PortAudio's thread and hands chunks over with `call_soon_threadsafe`. It
  asks the device for 16 kHz mono and converts whatever it gets instead:
  on this laptop the built-in array offers **48 kHz, 4 channels** and refuses
  16 kHz, so resampling is the normal path, not a fallback. Takes one channel
  rather than letting the driver downmix — at the venue a downmix would sum
  the speaker's microphone into whatever else is plugged into the interface.
- **`FileSource`** — the same interface over a recording, `realtime=True` by
  default. WAV is read with the standard library so the tests need nothing
  installed; other formats go through ffmpeg. This is what let the whole
  capture path be tested with no microphone at all.
- Resampling carries state across chunk boundaries and low-passes before
  decimating. Both details are tested, because without the first there is a
  click ten times a second and without the second everything above 8 kHz
  folds back into the speech band.
- **Level meter** — RMS and peak per chunk, in dBFS, on `/admin` at ~7 Hz off
  its own `/admin/level` endpoint. Clipping **latches for 1.5 s** so a single
  overloaded chunk is still lit when a page polling at 1 Hz next looks.
- **`--record`** is off by default (CLAUDE.md: the room has not consented).
  When on, the run writes `recordings/<date>-<time>.wav`. The time is in the
  name so a second run cannot overwrite the first one's audio.

`app/usage.py` — one JSON line per run in `logs/usage.jsonl`, appended from
the feed's `finally`, so a run the operator stops early is still recorded with
the seconds it used. `billable` is written into the line explicitly, and
replay and microphone runs are logged with `billable: false`: a ledger with
gaps in it invites the question of whether a run was simply forgotten.

`tools/list_devices.py` — the device list, `--probe` to test each device for
native 16 kHz, and `--listen` for a terminal level meter.

### `/admin` is now a three-source page

`source=replay` plays a capture through `CaptionState` — captions, no audio.
`source=mic` and `source=file` capture audio and measure it — audio, no
captions. **Phase 4 has the microphone but not the engine**, so a mic run
produces levels and seconds and nothing on the page. Phase 5 joins the two by
putting the real client between `_run_audio` and `CaptionState`; the source
does not change.

### Phase 4 acceptance: two of three

BUILD_SPEC's acceptance is "captions from a live mic show on a phone; admin
meter moves with speech; clipping is visible".

- **Meter moves with speech** — yes, checked live on device 18 (the built-in
  array) through the running server: `-60 dBFS` at rest rising to `-44 dBFS`
  on speech, `0` chunks dropped, `0` callback warnings. A 7.0 s `--record`
  run wrote a 16 kHz mono WAV.
- **Clipping is visible** — yes, but **proved by test, not by a live
  overload** (`test_clipping_latches_so_a_one_chunk_overload_is_seen`). The
  built-in mic cannot be driven into clipping without shouting into it; the
  real check is the venue interface with its gain up, and that is a run-day
  check with the USB box.
- **Captions from a live mic** — the code path exists as of Phase 5 and is
  tested against a fake socket end to end. **It has never run against the real
  Soniox server.** That is the one outstanding item in the project.

## Session details are private, and editable mid-talk

Speaker names, titles and the operator's notes are not public; this repo is.
`config/session.yaml` is now **gitignored**, with `config/session.example.yaml`
as the committed shape.

**History check, 2026-10-13: clean.** Every version of `config/session.yaml`
ever committed (three commits: `43fef92`, `d8eca4c`, `a46a6de`) has
`title: ""`, `speakers: []`, `companies: []`, `notes: ""` — only comments ever
changed. Verified by reading each blob, not just the diffs. **No history
rewrite is needed and none was done.**

Where they may appear: the Soniox `context`, and `/admin` behind its token.
Nowhere else — not the audience pages, the viewer socket, `/admin/status`,
the usage ledger, a response log, a capture, or an error message. Those carry
`SessionContext.summary()`, which is key names and counts.
`tests/test_session_privacy.py` enforces it with strings that cannot occur by
chance, and **it caught a real leak**: the raw response log was writing the
whole Soniox config, context and all, into its meta line.

Editing on `/admin`: title, speakers, companies, extra terms, notes. Save
writes the file atomically (temp file + `os.replace`) and updates memory. With
nothing running, the next Start uses it. With a session live, the page says
*"Changes saved, not applied yet"* and offers **Apply now**, which reconnects
through the **rollover path** — a graceful close, a new connection with the
new context, the ring buffer covering the join. No audio dropped, no gap
notice, no reconnect counted, `live` untouched. Extra terms merge with
`config/glossary.yaml`, which stays in git because it is public vocabulary.

## The two fixes from the phone test

**1. Paragraphs no longer break mid-sentence.** The size break used to cut at
the first word boundary past 360 characters, which is what produced
"AlphaFold / at the time...". It now looks *backwards* from the limit for the
last sentence end — `.` `?` `!` `؟`, falling back to `،` only when there is no
terminator at all — and cuts blind only when neither exists.

Three details that are not obvious:

- **The cut is retroactive.** The text after the last sentence end has already
  been appended, so it moves into the new paragraph. On the page that reads as
  the last line of a block dropping into the block below, once per paragraph.
  It costs nothing in the protocol: a diff already carries each paragraph's
  full text keyed by `id`, so a paragraph getting *shorter* is an ordinary
  update. The alternative — breaking at the first sentence end past a minimum
  — avoids the reflow but makes every paragraph about half the size the
  audience page was tuned for.
- **A terminator only counts with a space after it**, possibly through a
  closing quote. That keeps `3.5` and `stt-rt-v5.` from splitting, and puts
  the cut after the quote in `no one has been." `. It does not save
  "Dr. Smith"; the cost there is a paragraph that ends one word early.
- **A terminator in the first half of the budget is ignored.** Otherwise a full
  stop five characters in would produce a five-character paragraph and
  hard-break the rest anyway.

Measured on the four captures: **79 of 80 size-broken paragraphs now end at a
sentence boundary.** The one that does not is a 363-character span in
`run4-saudi`'s English stream with no terminator past the halfway floor, and a
test asserts that every blind cut is of exactly that kind.

**2. `speakers` in `config/session.yaml` is now documented where it will be
read** — a comment in the file itself, and a new "Proper nouns" section in
`docs/soniox-notes.md`. Still empty, still free, still the cheapest quality win
available: it is why run1 says `Goudat` for the surname Gawdat.

## Phase 5: the real client

`app/soniox_client.py` is an async iterator of response dicts — deliberately
the same shape as `replay.FakeSonioxClient`, so `CaptionState` and the
broadcast hub cannot tell which one they are attached to. `connect` is
injected and `websockets` is imported lazily, which is what keeps all 279
tests free and makes it impossible for a test to dial Soniox by accident.

- **Ring buffer**, 20 s. On reconnect everything Soniox never acknowledged is
  re-sent before live audio resumes. `final_audio_proc_ms` is per connection,
  so it is mapped back to a global offset; that mapping is what makes the
  resume point correct across repeated drops.
- **Backoff** 0.5s, 1s, 2s, 4s, then 8s. Reset once a connection has carried a
  response, so a later drop is treated as a new incident rather than the
  continuation of a failing chain.
- **All 14 documented `error_type` values classified**, asserted at import so
  a future edit cannot drop or double-classify one. Unknown types are retried
  but capped at three attempts, so an undocumented *fatal* error cannot loop.
- **Keepalive** every 20 s when no audio is flowing.
- **Idle auto-stop**: ten minutes under the silence threshold ends the stream
  and shows `paused`. The resume on the next sound is not in the spec and a
  talk needs it — nobody is watching `/admin` during a break.
- **`live` never goes false on a drop.** Only the operator clears it. The
  engine's health is a separate `status` field on the snapshot and diff, and
  the audience page shows `reconnecting` or `paused` on the chip while keeping
  the captions it already has. A drop also commits any uncommitted grey text,
  so it does not hang there for the length of the reconnect and arrive twice
  when the ring replays.

### One deliberate deviation

**Rollover closes before it opens.** The spec says "open a new connection,
start sending, then close the old one gracefully". This closes gracefully
first and replays the ring to cover the gap. The overlap costs a second
connection's billing on every rollover and two live sockets to reason about,
for a path that only runs after 280 minutes of streaming — and $10 does not
buy 280 minutes, so the overlap would be untestable complexity guarding an
event that cannot happen on this project.

### Two bugs the tests caught

- **A graceful end was read as the end of the talk.** When the client closes a
  stream on purpose, Soniox answers `{"finished": true}` and both tasks finish;
  reading the receiver's answer first meant a rollover looked like "the audio
  ran out". Effect: the session would simply stop after 280 minutes, and the
  idle pause would never resume. Both the rollover and pause tests fail
  against the old ordering.
- **`asyncio.sleep` does not advance the clock on Windows.** A 60-chunk
  scripted stream ran inside a single `time.monotonic()` tick, so a scaled-down
  idle timeout never fired while the detector was correct. The client now takes
  its clock as an argument. Worth remembering for any timing test in this repo.

### Two test changes worth knowing about

- **The `Bearer ` ban was tightened, not dropped.** Phase 3 asserted the string
  appeared nowhere in `app/`, which worked only while nothing in `app/`
  authenticated. The header *is* the documented auth mechanism, so the test now
  enforces the real invariant from CLAUDE.md: every `Bearer` must be built from
  the setting on the same line, never from a literal, and `sk-` still appears
  nowhere.
- **The admin page says "the engine", not the engine's name.** CLAUDE.md's grep
  of served files is blunt on purpose, and `/admin/status` is covered by the
  same check, so `session.engine` is deliberately absent from the served
  payload. `engine_state`, `lag_seconds` and `streamed_seconds` carry what the
  operator needs.

## Phone test setup (checked today)

Server bound to all interfaces, replaying `run1` at real time:

```powershell
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The token comes from `.env` in the repository root and nowhere else - see
the rule in CLAUDE.md. The server refuses to start without one.

- **Phone URL: `http://172.16.10.137:8000/`** — the laptop's address on the
  `EMP 2` Wi-Fi. It is DHCP, so **check it again before the meeting**:
  `Get-NetIPAddress -AddressFamily IPv4`.
- The phone must be on the same Wi-Fi. That network's profile is **Public**,
  so Windows Firewall blocks inbound by default. There are already inbound
  Allow rules for a `uv` Python, but for
  `cpython-3.12.13-...`, and this venv runs `cpython-3.12-...` — a different
  path, so those rules probably do not cover it. A port rule is
  path-independent and is the one to use (elevated PowerShell, once):

  ```powershell
  New-NetFirewallRule -DisplayName "Sabtiah captions 8000" `
    -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8000 `
    -Profile Private,Public
  ```

  Delete it after the meeting with
  `Remove-NetFirewallRule -DisplayName "Sabtiah captions 8000"`.
- Verified from the laptop over `172.16.10.137`: `/`, `/screen`,
  `/static/style.css` and `/static/app.js` all 200, `/admin` 401 without a
  token. Paced replay tracked real time exactly — 18.1 s elapsed, 18.1 s of
  audio.
- `run1` is five minutes long and then stops. Restart it from `/admin`, or:

  ```powershell
  curl -X POST "http://127.0.0.1:8000/admin/start?token=<ADMIN_TOKEN>&capture=run1.jsonl&paced=true"
  ```

**Verified on Hamza's phone on 2026-10-13**: the page loaded over the office
Wi-Fi, captions streamed, and the English translation read well. That settles
the firewall and the rendering at phone width. Still unverified: iOS Safari
specifically (if the phone that was used is Android, or the other way round),
and three phones at once.

## Two deliberate deviations from BUILD_SPEC

1. **A diff carries `base`**, the sequence number it was computed from
   (`captions.py: diff_since`). Without it a client can tell a diff is newer
   than its state but not whether anything fell out in between.
   `base > my seq` is the loss test.
2. **The audience page does not use `dir="auto"`.** It takes a paragraph's
   base direction from its first strong character, so an Arabic caption
   beginning `RAG يعني...` would lay out left-to-right — and a caption
   starting with a Latin technical term is exactly what
   `config/glossary.yaml` is meant to produce. Each stream declares its own
   base direction instead.

## Open items

1. **A short live re-run is owed**, to confirm the run7 fixes: the ring now
   covers a real outage, the join reads `behind ChatGPT` rather than
   `behindChatGPT`, `dropped_seconds` stays 0, and the error clears after
   recovery. Also the two unsettled terms (`agents`, `agent`, `ChatGPT`).
   Checklist in `docs/resilience-test.md`.
2. ~~**A replay leaves the captions behind**~~ — **decided and confirmed.**
   At most 10 s is replayed; a longer outage drops its oldest audio and says
   so in both streams. Seen working on run8: a 27.3 s outage, 18.6 s dropped
   and announced, captions back live.
3. ~~**`translation_terms` is untested**~~ and ~~**the terms clip is
   owed**~~ — **both closed by run7.** The experiment was done with live
   speech instead of a recording: Hamza said the terms into the microphone,
   which is what the clip was only ever a substitute for. `transformers`
   came back as `المحوّلات` on run6 and as `transformers` on run7 with nothing
   changed but the glossary. `recordings/terms-1min.m4a` was never created and
   is no longer needed.

   The Phase 1 statement that **nothing stays in Latin script is superseded**;
   it was measured with an empty `translation_terms`. The two words that still
   do not obey it are `agents` and `AI`, and they are open item 1, not this
   one.

4. ~~**The per-minute rate is not recorded anywhere**~~ — **answered
   2026-10-13**: $0.18/hour, $0.003/minute, from soniox.com/pricing. Put
   `SONIOX_RATE_PER_MINUTE=0.003` in `.env` to turn the dollar figures on;
   it is in `.env.example` with the source beside it. The budget rules above
   and in CLAUDE.md are rewritten around it.
5. **Acceptance 3 and 4 are still unrun.** The invalid-key check needs one
   short live start; the 60-minute soak needs no credit at all and has not
   been done.
6. **The headset microphone reads silence through its own device entry.**
   run6 and run7 both worked through `Microsoft Sound Mapper` (device 0), so
   this is no longer blocking - but Hamza asked for live tests on the
   headset rather than the laptop array. It is device **17** (`Microphone
   (HyperX Virtual Surround Sound)`, WASAPI, 48 kHz, 4 ch). Checked on
   2026-10-13 with `--listen`: peak 0.001, no signal - and devices 8, 26 and
   27 (the other HyperX and analog-jack entries) are silent too, while the
   built-in array picked up room noise minutes earlier, so capture and
   permissions are fine. The headset is not connected, not powered, or muted.
   Plug it in and re-run `uv run python tools/list_devices.py --listen 5
   --device 17` before the live test.
7. **`agents`/`agent` is a known limitation, accepted.** Both are in
   `translation_terms` and both get translated. run8 settled the shape of it:
   singular `agent` became `الوكيل`, which is *correct*, where run7's plural
   became `أجهزة` ("devices"), which is not. A settled Arabic equivalent beats
   `translation_terms`; whether that hurts depends on the sense the model
   picks.
8. **One Arabic phrase landed in the English stream on run7** and has never
   recurred. run8 had the raw log and the same conditions - a mid-run language
   switch, 723 Arabic original tokens - and leaked nothing. Not systematic;
   reasoning in `docs/soniox-notes.md`.
9. **Saudi dialect is still unmeasured.** `recordings/saudi-5min.m4a` is MSA
   news register about Yemen with zero distinctively Saudi lexis. Needs
   casual Saudi conversation. The audience is Saudi; the only dialect
   measured is Egyptian.
10. **Long sessions untested against the real server.** The rollover, the
   keepalive and the idle auto-stop all have tests, but none has run for real.
   The 60-minute `FileSource` soak in BUILD_SPEC's acceptance 4 needs no
   credit and has not been done.
11. **`config/session.yaml` is empty.** Filling `speakers` would likely fix
   `جودت` → "Goudat" in run1. Cheap win, no API cost.
12. **Three phones at once, and the other mobile browser, untested.** One
   phone works. The spec wants iOS Safari and Android Chrome at 360 px.
13. **The venue USB interface has never been plugged in.** `MicSource` takes
   `--channel` and resamples, which is what a multi-channel interface needs,
   but every live check so far has been the laptop's built-in array.

## Half-finished

Nothing in the code. Three loose ends worth knowing:

- **Some Phase 1 numbers are not reproducible from the repo.** The committed
  `--analyze` covers the five questions, but the per-distinct-token delay
  figures, the attribution-free `<end>` check and the word-boundary dialect
  counts came from throwaway scripts in a scratchpad that is **not** in git.
- **One cosmetic traceback on Windows.** An abrupt client disconnect can
  print `ConnectionResetError: [WinError 10054]` from asyncio's own
  `_ProactorBasePipeTransport._call_connection_lost`. It is inside asyncio
  rather than this code and the server kept serving. Worth recognising on run
  day so it is not mistaken for a fault.
- **`logs/usage.jsonl` is gitignored**, so a fresh clone starts the ledger at
  zero and `/admin` will under-report spend until it is seeded again. The
  seeding is reproducible from `docs/captures/*.jsonl`; the snippet is in the
  commit for Phase 4.
