# Resilience test results (Phase 5)

BUILD_SPEC asks for four by-hand checks against a live stream. Each one costs
credit and a stopwatch, and $10 has to cover the demo, so each is split in two:

1. **The mechanism** is reproduced offline against a scripted socket, in
   `tests/test_soniox_client.py`. That is where the logic is proved.
2. **The live confirmation** only has to show that the real server behaves
   like the fake. It is cheap, short, and listed below as outstanding.

Status: **offline half done. Live half run once, on run7, and it found two
bugs.** See the run7 section below - the headline is that a 10 s Wi-Fi drop
produced a 32.5 s outage, which a 20 s ring cannot cover.

| # | BUILD_SPEC acceptance | Offline | Live |
| --- | --- | --- | --- |
| 1 | Wi-Fi off 10 s mid-sentence, back on: captions resume, the sentence spoken during the drop appears | ✅ `test_a_ring_that_outlives_the_outage_loses_nothing` | ❌ **FAILED on run7**, fixed, **needs a re-run** |
| 2 | Network dead 2 min: admin shows reconnecting, pages show an indicator, recovery is automatic | ✅ `test_a_long_outage_backs_off_and_keeps_trying`, `test_the_engine_state_reaches_the_pages_without_clearing_live` | ⚠️ passed on run7 for a 32.5 s outage, not 2 min; stale-error flaw fixed |
| 3 | Invalid API key: admin shows a clear error, no retry loop | ✅ `test_a_fatal_error_stops_without_retrying` (all 8 fatal types), `test_a_fatal_error_stops_the_session_and_shows_up_on_admin` | ❌ not run |
| 4 | 60 minutes continuous from `FileSource`, no memory growth beyond the 200-segment cap | ⚠️ partial — the cap and the ring bound are tested, the hour is not | ❌ not run (needs no credit) |

## What the offline tests actually prove

**Acceptance 1, no audio lost.** The ring buffer holds the last 20 s. On
reconnect the client replays everything Soniox never acknowledged, mapping
`final_audio_proc_ms` (which is per connection) back to a global offset so the
resume point is right across any number of drops. The test asserts the second
connection receives at least the chunks the first one never got through.

**Acceptance 2, automatic recovery.** Backoff is 0.5s, 1s, 2s, 4s, 8s and then
8s for ever. A 1.2 s simulated outage produces 2-6 connection attempts, not
thousands. `live` stays true throughout — a drop is not the end of the talk —
and the page's status chip goes to `reconnecting` instead.

**Acceptance 3, no retry loop.** All 14 documented `error_type` values are
classified, and the classification is parametrized over both sets so every one
of them is exercised. Branching is on `error_type` only; a test feeds a fatal
type with a *retryable-sounding* message and asserts it still stops.

**Acceptance 4, bounded memory.** The paragraph cap (200 per stream) and the
ring bound (20 s) are both tested directly. What is **not** tested is an hour
of wall-clock running, so a slow leak outside those two structures would not
be caught. See below.

## run7, 2026-10-13: the first real outage — acceptance 1 and 2, FAILED then fixed

A live run of 184 s (172.9 s billable) with a deliberate Wi-Fi drop. This is
the first time the resilience code met a real network, and it found two bugs
that every offline test had passed.

### What happened, from `docs/live/run7.jsonl`

| | |
| --- | --- |
| Connection lost | 562.0 s (`ConnectionClosedError`) |
| Reconnect attempts | 7, at +0.5, +1.0, +2.1, +4.1, +7.8, +7.8 s |
| Failure during the outage | `gaierror: [Errno 11001] getaddrinfo failed` — DNS |
| Connection restored | 594.5 s |
| **Outage length** | **32.5 s**, from a Wi-Fi drop of about 10 s |
| Audio replayed on reconnect | 21.7 s, sent in ~1 s (`streamed` 41.1 → 62.8) |
| Lag after recovery | 3.5 s → 21.7 s, and **stayed ~20 s for the rest of the run** |

### Acceptance 1 FAILED: the sentence spoken during the drop did not appear

`the same architecture behind ChatGPT` never reached the page, along with
roughly ten seconds after it.

**The ring replay was not broken — it fired correctly.** `streamed` jumping
41.1 → 62.8 s in one second is 21.7 s of buffered audio going out on the new
connection. The arithmetic is what failed:

```
outage   32.5 s   audio captured with nowhere to send it
ring     20.0 s   what the buffer holds
replayed 21.7 s   what went out
lost    ~12.5 s   the OLDEST audio - the speech right after the drop
```

The ring discards oldest-first, so the twelve seconds it dropped were exactly
the ones the speaker noticed missing. BUILD_SPEC's 20 s was sized against its
own acceptance test of a *10 s drop*; a 10 s drop does not cost 10 s, because
DNS stays broken after the radio comes back.

**Two fixes.** The ring is now **45 s**, which covers this outage with room to
spare at a cost of about 1.4 MB. And failures that never reached Soniox — DNS,
a refused socket, a dead route — now back off to a **2 s cap instead of 8 s**;
the attempt that finally succeeded on run7 was scheduled 8 s after the one
before it, so the laptop may have had working DNS for most of that time and
simply sat there. Errors Soniox *reported* keep the 8 s cap.

A third fix, found while reading the code for the first two:
`ConnectionClosedError` was counted as an *unknown* error, which is capped at
three attempts. Three drops in one session would have stopped the stream for
good, and a conference Wi-Fi gives you three drops. It is now classified as a
network error, like DNS.

Tests: `test_an_outage_longer_than_the_ring_loses_speech` reproduces the loss
by numbering every chunk and checking which ones reached the server;
`test_a_ring_that_outlives_the_outage_loses_nothing` is the fix.

### The joined-sentence bug: `behindMicrosoft`

The text that did arrive was glued onto the previous sentence with no space.

A reconnect opens a **new** Soniox stream, and the first token of a stream has
no leading space — it is the start of an utterance as far as the engine is
concerned. `CaptionState` only strips or inserts spacing when a paragraph is
empty, so it concatenated directly. `CaptionState.mark_gap()` now records the
discontinuity and separates the next token; it replaces the bare
`finalize_partials()` call on a drop, so it still commits grey text too.

Tested in `tests/test_captions.py` with the literal run7 string.

### Acceptance 2 PASSED, with one flaw

Recovery was automatic and needed no operator action, the backoff behaved
exactly as specified, and `live` stayed true throughout so the pages kept
their captions. The engine state went `streaming → reconnecting → streaming`
and the audience chip followed.

The flaw: **`gaierror` was still showing as the last error when the run
finished healthy.** It was correctly classified — `fatal: false`, retried
seven times, never treated as terminal — but a session that has recovered
should not look broken on `/admin`. `last_error` now clears once a connection
carries a response, and the incident moves to `recovered_errors` so it is
still visible after the fact. `engine_recovered` on the status says how many
times that happened.

### The replay-versus-delay decision (Hamza, 2026-10-13)

run7 raised a question the spec does not answer. Soniox acknowledges at about
real time, so replaying N seconds leaves the captions N seconds late **for the
rest of the session** - run7 replayed 21.7 s and then ran ~20 s behind for its
remaining 110 seconds. A bigger ring makes that worse, not better.

**Decision: prefer staying live.**

- At most **10 s** is replayed after a reconnect (`MAX_REPLAY_SECONDS`). A
  short drop still loses nothing, which is the common case.
- A longer outage drops its **oldest** audio and keeps the newest, so the
  captions resume near real time rather than narrating the past.
- The gap is announced: `CaptionState.add_notice()` inserts a short paragraph
  in **both** streams - `[connection interrupted]` and `[انقطع الاتصال]` -
  styled as an aside so it cannot be mistaken for speech. One notice per
  outage, however many reconnect attempts it took, and none at all when
  nothing was lost.
- `dropped_seconds` is on `/admin` and survives the session being stopped.

The ring is back to BUILD_SPEC's **20 s**; it only has to outlive the replay
window, and the 45 s of the previous commit was sized for a goal that no
longer applies.

Tested in `test_an_outage_longer_than_the_replay_window_drops_the_oldest`,
`test_a_drop_inside_the_replay_window_still_loses_nothing`,
`test_the_audience_is_told_when_audio_was_dropped` and
`test_a_long_outage_puts_a_notice_on_the_page`.

### Still unmeasured

- The notice and `dropped_seconds` have never been seen on a real run.
- Acceptance 4, the 60-minute soak, still has not been run. It needs no credit.

## Things found while writing these tests

- **A graceful end was being read as the end of the talk.** When the client
  closes a stream on purpose — a rollover, or the idle pause — it sends the
  empty text frame, Soniox replies `{"finished": true}`, and both the sender
  and receiver tasks complete. The first version read the receiver's answer
  first and concluded the session was over. The effect would have been a
  session that simply stops after 280 minutes instead of rolling over, and an
  idle pause that never resumes. Fixed by asking the sender first; both the
  rollover and pause tests fail against the old ordering.
- **The idle auto-stop cannot be tested by shortening it and sleeping.** On
  Windows, `asyncio.sleep(0.01)` routinely returns without `time.monotonic()`
  having advanced at all — a whole 60-chunk scripted stream ran inside a single
  clock tick, so a 0.15 s timeout never fired while the detector was perfectly
  correct. The client now takes its clock as an argument and the test drives
  time explicitly. Worth remembering for any other timing test in this repo.

## Still to do live (ask Hamza first — this spends credit)

One short run, after the run7 fixes:

1. **Tick "Log raw responses"** on `/admin` before starting. It writes
   `logs/live-<date>.jsonl`, which replays as a test fixture and is the only
   way to answer token-level questions afterwards — the Arabic-in-English
   phrase from run7 is still unexplained for exactly this reason.
2. Say `agent` (singular) in a sentence. run7 only ever tested the plural.
3. Drop Wi-Fi mid-sentence for ~10 s and keep talking. Confirm:
   - the join reads `... behind ChatGPT`, not `behindChatGPT`;
   - captions come back **near real time**, not twenty seconds late;
   - if anything was dropped, `[connection interrupted]` appears in both
     streams and `dropped_seconds` is non-zero on `/admin`;
   - the last error has cleared and `engine_recovered` is 1.

Separately, and with a deliberately wrong key, acceptance 3 is still unrun
live: one start, one error, no retry loop.

Acceptance 4, the 60-minute soak, **needs no credit** — run it from
`FileSource` against the replay path, which exercises `CaptionState` and the
broadcast hub for an hour without the engine. Still not done.
