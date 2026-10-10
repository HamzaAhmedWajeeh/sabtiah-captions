# Soniox notes (Phase 1)

Status: **probe built and verified, not yet run against Soniox.** The five
questions in BUILD_SPEC.md section 4 are unanswered because the run needs two
things that are not on this machine yet: a `SONIOX_API_KEY` in `.env`, and an
audio recording. See [What is still needed](#what-is-still-needed).

## API facts re-verified against the live docs (2026-10-10)

Checked <https://soniox.com/docs/api-reference/stt/websocket-api> and
<https://soniox.com/docs/stt/rt/real-time-translation>. Everything in
BUILD_SPEC section 3 still holds: endpoint, `Authorization: Bearer` header,
deprecated `api_key` config field (refused from 15 Jan 2027), JSON config as
the first text frame then binary audio, empty text frame to end, 40 s keepalive
(idle close is WebSocket 1001), 300-minute cap, and the response/token/error
shapes.

Three things the docs add that the spec does not mention:

1. **The error-type list is longer than Phase 5's draft classification.** Full
   documented set: `invalid_request`, `model_not_available`, `unauthenticated`,
   `organization_balance_exhausted`, `organization_monthly_budget_exhausted`,
   `project_monthly_budget_exhausted`, `permission_denied`,
   `temp_api_key_session_expired`, `request_timeout`, `max_duration_reached`,
   `limit_exceeded`, `max_concurrent_connections_reached`, `internal_error`,
   `service_unavailable`.

   Phase 5 should add: `limit_exceeded` and
   `max_concurrent_connections_reached` as retryable with backoff (transient
   capacity, not our fault), and `model_not_available` plus
   `temp_api_key_session_expired` as fatal (no amount of retrying fixes
   either).

2. **An empty *binary* frame is treated as audio, not as end-of-stream.** Only
   an empty *text* frame ends the stream. `send_loop` therefore skips a
   zero-length final read rather than forwarding it.

3. **Translation tokens carry no timestamps** — confirmed explicitly: "Translated
   tokens do not include timestamps. They are generated after their spoken
   tokens and follow the same sequence." This is the root cause of question 5
   being hard: there is no `end_ms` on a translation token to measure against,
   so lag has to be measured from the *source* segment's last `end_ms` to the
   wall-clock arrival of the translation.

**The docs answer none of the five questions.** Notably `source_language`
(question 4) is listed as a token field in the WebSocket reference but is not
described anywhere in the translation documentation, and the translation page
says nothing about end markers, endpoint interaction, two-way direction
selection, or mixed-language handling. These have to be measured.

## How the probe measures things

`tools/probe_soniox.py` has two modes.

```bash
# capture
uv run python tools/probe_soniox.py --file <audio> --out docs/captures/run1.jsonl

# analyze a capture (prints markdown; --report also writes it)
uv run python tools/probe_soniox.py --analyze docs/captures/run1.jsonl \
    --report docs/captures/run1.md
```

Audio is decoded by ffmpeg to 16 kHz mono s16le and sent in 100 ms chunks
**paced at real time**. That pacing is what makes latency measurable: the audio
at offset X ms leaves the laptop at roughly X ms after stream start, and every
captured response records `recv_offset_ms` on the same clock. So

```
wall-clock latency = recv_offset_ms - token.end_ms
```

Each JSONL line is `{recv_at, recv_offset_ms, sent_ms, response}` with the raw
response untouched, so the capture stays usable as a Phase 2 fixture no matter
what the analyzer later decides to compute.

Segment attribution: an original `<end>` closes a segment. A translation token
is attached to the newest segment that has original text, so a translation
arriving after its `<end>` still lands on the segment it belongs to.

**Known limitation of that heuristic:** if a late translation arrives *after*
the next segment's original tokens have already started, it will be
misattributed to the newer segment. Whether that happens is itself part of
question 1. If the capture shows translations routinely overlapping the next
segment, Option A is off the table and `captions.py` must not try to pair a
translation with a specific source line.

## The five questions

Unanswered until the probe runs. Each will be filled in with quoted token
excerpts from the JSONL, as the spec requires.

### 1. Do translation tokens for a segment ever arrive after that segment's `<end>`? How often, how late?

_Pending capture._ The analyzer reports this as a per-segment table plus
min/median/max lateness in ms. **This is the question that picks the caption
design.** If translations reliably land at or before `<end>`, Option A
(line-aligned `orig` + `translation`) becomes possible; if they trail, Option B
(two independent rolling streams) is the only safe choice.

### 2. Is there any end marker on the translation side?

_Pending capture._ The analyzer counts `<end>` tokens whose
`translation_status` is `"translation"`. The docs do not mention one, so the
expectation is zero — which would mean the translated stream has no native
paragraph boundary and Option B's "count pending endpoints" approach is
necessary.

### 3. For "اليوم بنشرح RAG and agentic workflows": what `language` do the original tokens carry? Per token or per segment? Does the translation keep "RAG" in English?

_Pending capture._ The analyzer lists every mixed-script segment with its
per-token `language` distribution, the `language` on each Latin-script token,
and whether the translation retained Latin script. This decides whether
Option B's "assign the segment to its majority language" rule is needed to stop
English words inside an Arabic sentence leaking into the English stream as
fragments.

### 4. What does `source_language` contain on translation tokens?

_Pending capture._ The analyzer cross-tabulates `source_language` against
`language` over all translation tokens. Undocumented, so it may be absent
entirely — in which case direction has to be inferred from `language`.

### 5. Typical delay between a word's `end_ms` and its translation token arriving?

_Pending capture._ The analyzer reports median/p90/min/max for three lags:
original final token after the word was spoken, first translation token after
the segment's last word, and last translation token after the segment's last
word. The third is the number that determines how far behind the speaker the
translated caption actually runs.

## What is still needed

1. **`SONIOX_API_KEY`.** `cp .env.example .env` and paste the key from the
   Soniox console. While there, set a monthly budget cap (BUILD_SPEC section 8)
   — budget-exhausted errors are handled as fatal, so a cap fails safe.
2. **Audio.** Either a recording from a past Sabtiah session, or a phone
   recording of ten sentences: 3 Saudi Arabic, 3 English, 4 mixed (Arabic with
   English tech terms), including "اليوم بنشرح RAG and agentic workflows". Any
   format — ffmpeg handles the decode. Speak at normal talk pace with a pause
   between sentences so endpoint detection has something to find.

With both in place the capture, the analysis, and the rest of this document
take a few minutes.

## Verified without Soniox

What has been exercised end to end against a stub socket and a synthetic
capture:

- ffmpeg decode of a 44.1 kHz source to 16 kHz mono s16le: 30 frames of exactly
  3200 bytes for 3 s of audio, 3.00 s wall clock — pacing is real time.
- The stream ends with an empty text frame; `sent_ms` tracks bytes exactly.
- Responses are captured to JSONL with all four keys; `{"finished": true}` and
  error responses both terminate the receive loop, and an error prints its
  `error_type`/`error_code`/`request_id`.
- The readable rendering shows finals solid, non-finals in brackets, `<end>` as
  `<END>`, and never accumulates `<end>` into caption text.
- The Soniox `context` object builds from `config/glossary.yaml` +
  `config/session.yaml`.
- `tests/test_probe_analysis.py` (10 tests) locks the analyzer's arithmetic
  against a known synthetic capture: segment bounds, non-final tokens not
  double-counted, late-translation attribution, lateness, lag, and
  `source_language` pairing.

The synthetic capture's **timings are invented**. It validates the measuring
instrument, not Soniox's behavior, and it is deliberately *not* in
`tests/fixtures/` — Phase 2 must be built against real captures only.
