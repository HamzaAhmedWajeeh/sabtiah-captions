# Resilience test results (Phase 5)

BUILD_SPEC asks for four by-hand checks against a live stream. Each one costs
credit and a stopwatch, and $10 has to cover the demo, so each is split in two:

1. **The mechanism** is reproduced offline against a scripted socket, in
   `tests/test_soniox_client.py`. That is where the logic is proved.
2. **The live confirmation** only has to show that the real server behaves
   like the fake. It is cheap, short, and listed below as outstanding.

Status: **offline half done, live half not run.** No live run has happened; as
of 2026-10-13 the project has spent 22.0 minutes, all of it in Phase 1.

| # | BUILD_SPEC acceptance | Offline | Live |
| --- | --- | --- | --- |
| 1 | Wi-Fi off 10 s mid-sentence, back on: captions resume, the sentence spoken during the drop appears | ✅ `test_a_mid_sentence_drop_reconnects_and_loses_no_audio` | ❌ not run |
| 2 | Network dead 2 min: admin shows reconnecting, pages show an indicator, recovery is automatic | ✅ `test_a_long_outage_backs_off_and_keeps_trying`, `test_the_engine_state_reaches_the_pages_without_clearing_live` | ❌ not run |
| 3 | Invalid API key: admin shows a clear error, no retry loop | ✅ `test_a_fatal_error_stops_without_retrying` (all 8 fatal types), `test_a_fatal_error_stops_the_session_and_shows_up_on_admin` | ❌ not run |
| 4 | 60 minutes continuous from `FileSource`, no memory growth beyond the 200-segment cap | ⚠️ partial — the cap and the ring bound are tested, the hour is not | ❌ not run |

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

A single run of **2 minutes or less** covers 1, 2 and 3:

1. Start a live session from `/admin` with the headset mic.
2. Speak a sentence; confirm captions on the phone.
3. Turn Wi-Fi off mid-sentence for ~10 s, keep talking, turn it back on.
   Confirm the sentence spoken during the drop appears, and that the phone
   showed `reconnecting` rather than going blank.
4. Stop. Then restart with a deliberately wrong key and confirm `/admin` shows
   `unauthenticated` with exactly one connection attempt.

Item 4 of the spec (the 60-minute run) does not need credit: run it from
`FileSource` against the replay path, which exercises `CaptionState` and the
broadcast hub for an hour without the engine. **Not yet done.**
