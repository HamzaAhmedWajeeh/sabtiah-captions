# Checkpoint — 2026-10-13

**The plan changed.** Saturday Oct 17 is a **conversation with the Sabtiah
organizer**, not a live demo. What it needs is a working demo on Hamza's
phone, shown to one person. The first real session is about a week later,
roughly Sat Oct 24.

That moves the deadline that mattered. Nothing is due on a stage this week,
so Phase 5 resilience and the Phase 8 tunnel have a week more than they had
this morning — and the thing to protect now is a phone that shows captions
convincingly over Wi-Fi, which it does today off a replay.

Phases 0–4 done. Phase 5 (resilience, the real Soniox client) next.

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
  `app/usage.py` writing `logs/usage.jsonl`. Details below.

**206 tests green, all offline** — the 145 from Phases 2–3 plus 61 for
Phase 4. No test touches the network.

## Soniox audio spent: 22.0 minutes — unchanged

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
- **Captions from a live mic** — **not possible yet.** It needs Phase 5. This
  is the one acceptance item carried forward.

## Phone test setup (checked today)

Server bound to all interfaces, replaying `run1` at real time:

```powershell
$env:ADMIN_TOKEN = "<throwaway>"
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

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
  curl -X POST "http://127.0.0.1:8000/admin/start?token=<token>&capture=run1.jsonl&paced=true"
  ```

**Not verified: an actual phone.** Everything above was checked from the
laptop. Whether iOS Safari or Android Chrome renders it well at 360 px, and
whether the firewall lets the phone in, are both open until a phone is
pointed at that URL.

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

1. **`translation_terms` is still untested — and the clip is still not on
   disk.** The one pass was requested again on 2026-10-13 with
   `recordings/terms-1min.m4a` named as the input. **That file does not
   exist**, and nothing audio-shaped has landed in `recordings/`,
   `~/Downloads`, `~/Desktop` or `~/Documents` since 2026-10-11. The run did
   not happen. The only English audio on disk is the Rogan cut, which
   `docs/soniox-notes.md` records as containing none of the glossary terms,
   so streaming it would spend credit to learn nothing. Until the clip
   exists, treat Latin-script preservation in Arabic captions as **not
   working**: `RAG`/`MCP`/`LLM` will be transliterated.
2. **The per-minute Soniox rate is not recorded anywhere**, so `/admin` shows
   counted minutes and no dollar figure. `app/usage.py` will show dollars as
   soon as `SONIOX_RATE_PER_MINUTE` is set in `.env` — one number from the
   Soniox console turns it on. Deliberately not guessed: a made-up rate
   printed to two decimal places reads as a measurement.
3. **Saudi dialect is still unmeasured.** `recordings/saudi-5min.m4a` is MSA
   news register about Yemen with zero distinctively Saudi lexis. Needs
   casual Saudi conversation. The audience is Saudi; the only dialect
   measured is Egyptian.
4. **Long sessions untested.** The 300-minute cap, the 280-minute rollover
   and the 20 s keepalive have never run. Phase 5.
5. **`config/session.yaml` is empty.** Filling `speakers` would likely fix
   `جودت` → "Goudat" in run1. Cheap win, no API cost.
6. **Phone browsers untested** — see above. The spec wants iOS Safari and
   Android Chrome at 360 px.
7. **The venue USB interface has never been plugged in.** `MicSource` takes
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
