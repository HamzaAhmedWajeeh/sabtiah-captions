# Checkpoint — 2026-10-11

Six days to the demo (Saturday Oct 17). Phases 0–3 done, Phase 4 next.

## Done

- **Phase 0** — skeleton, `pyproject.toml`, `.gitignore`, FastAPI stub.
- **Phase 1** — all five section 4 questions answered from five live captures.
  Findings in `docs/soniox-notes.md`; **read it before touching caption
  logic.** Two probe bugs fixed along the way (cp1252 crash on Arabic to a
  redirected stdout; a hang + leaked ffmpeg on any early-ending stream).
- **Phase 2** — `app/captions.py` (pure, Option B, two per-language streams)
  and `app/replay.py` (`FakeSonioxClient` over saved JSONL).
  `tools/render_capture.py` renders a capture as the audience would see it.
- **Phase 3** — `app/broadcast.py` (viewer hub), `app/main.py` (routes, the
  socket, and the replay session), and the three pages: `web/index.html` +
  `app.js`, `web/screen.html` + `screen.js`, `web/admin.html` + `admin.js`,
  all on one `style.css`. Driven entirely by `FakeSonioxClient`.

**145 tests green, all offline** — 69 from Phase 2, 28 broadcast, 48 server.
No test touches the network.

## Soniox audio spent: 22.0 minutes — unchanged

Phase 3 cost nothing and **structurally cannot**: no module it added can open
an outbound connection. Every check below ran off saved captures.

## Next: Phase 4 — live audio capture

`app/audio_source.py` (`MicSource`, `FileSource`), `tools/list_devices.py`,
the RMS level meter for the admin page, and optional `--record`. This is the
first phase that needs the USB interface plugged in.

Phase 3 left the seam ready: `CaptionSession._run_replay` in `app/main.py` is
the only thing that knows where responses come from. Phase 4 adds a sibling
`_run_mic`, and Phase 5 swaps `FakeSonioxClient` for the real client behind
the same `start`/`stop`/`status`.

## What Phase 3 verified, and how

Acceptance was checked against a running server, not just unit tests.

- Three viewers at once (two audience pages + `/screen`), one real-time
  replay of `run1`: `viewers 3, rejected 0, dropped 0`, and
  `audio_ms 61600` at `elapsed 61.6 s` — paced replay tracks real time exactly.
- `run4-saudi` pushed through unpaced: **1,658 responses, 1,644 state changes,
  three live viewers, nothing dropped, no console errors.** Its single
  endpoint-free 3,974-character segment rendered as 11 Arabic paragraphs
  (longest 365 chars), so the soft size break works end to end.
- Toggles, A−/A+, scroll-back and "Jump to live" all work; view and text size
  persist in localStorage. No horizontal overflow.
- **Kill and restart the server**: pages go to `reconnecting`, keep showing
  the last captions, reconnect on their own, and resync — the stale paragraphs
  are dropped because a snapshot is authoritative.
- Route checks: `/static/admin.html` is 404, `/admin` is 401 with no token,
  with a wrong token, and with an **unset** `ADMIN_TOKEN`.

## Two deliberate deviations from BUILD_SPEC

1. **A diff now carries `base`**, the sequence number it was computed from
   (`captions.py: diff_since`). The spec says a client that "sees a `seq` gap"
   should resync, but without `base` a client can tell a diff is newer than
   its state and *not* whether anything fell out in between. `base > my seq`
   is the loss test; `tests/test_broadcast.py` proves diffs are contiguous.
2. **The audience page does not use `dir="auto"`.** The spec suggests it for
   mixed content. `dir="auto"` takes a paragraph's base direction from its
   first strong character, so an Arabic caption beginning `RAG يعني...` would
   lay out left-to-right — and a caption starting with a Latin technical term
   is exactly what `config/glossary.yaml` is meant to produce. Each stream
   declares its own base direction instead and the bidi algorithm places
   embedded Latin runs correctly. Confirmed on screen: `to fold one protein`
   renders LTR inside an RTL Arabic paragraph.

## Open items

1. **`translation_terms` is still untested, and still the highest-value
   experiment left.** It needs ~60 s of English containing the glossary terms.
   `recordings/terms-1min.m4a` **does not exist yet** — the one pass requested
   on 2026-10-11 could not run. The only English audio on disk is the Rogan
   cut, and `docs/soniox-notes.md` records that it contains none of the terms,
   so streaming it would spend credit to learn nothing. Until the clip exists,
   treat Latin-script preservation in Arabic captions as **not working**:
   `RAG`/`MCP`/`LLM` will be transliterated.
2. **Saudi dialect is still unmeasured.** `recordings/saudi-5min.m4a` is MSA
   news register about Yemen with zero distinctively Saudi lexis. Needs casual
   Saudi conversation. The audience is Saudi; the only dialect measured is
   Egyptian.
3. **Live microphone capture untested** — every capture came through ffmpeg
   from a file. That is Phase 4.
4. **Long sessions untested.** The 300-minute cap, the 280-minute rollover and
   the 20 s keepalive have never run.
5. **`config/session.yaml` is empty.** Filling `speakers` would likely have
   fixed `جودت` → "Goudat" in run1. Cheap win, no API cost.
6. **Phone browsers untested.** Everything above was checked in Chrome on the
   laptop. The spec wants iOS Safari and Android Chrome at 360 px, and
   "3 phones on the same Wi-Fi" needs the Phase 8 tunnel or a LAN bind.

## Half-finished

Nothing in the code. Two loose ends worth knowing:

- **Some Phase 1 numbers are not reproducible from the repo.** The committed
  `--analyze` covers the five questions, but the per-distinct-token delay
  figures, the attribution-free `<end>` check and the word-boundary dialect
  counts came from throwaway scripts in a scratchpad that is **not** in git.
  The conclusions are in `docs/soniox-notes.md` with their inputs; re-deriving
  them means rewriting those scripts.
- **One cosmetic traceback on Windows.** An abrupt client disconnect can print
  `ConnectionResetError: [WinError 10054]` from asyncio's own
  `_ProactorBasePipeTransport._call_connection_lost`. It fired once across a
  session of repeated reloads, is inside asyncio rather than this code, and
  the server kept serving. Worth recognising on run day so it is not mistaken
  for a fault; silencing it is not worth a code change.
