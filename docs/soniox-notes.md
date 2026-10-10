# Soniox notes (Phase 1)

Status: **the probe reaches Soniox and is rejected: the account has no funds.**
The key authenticates, the config is accepted, and the server answers with
`organization_balance_exhausted` (HTTP 402) before transcribing any audio. The
five questions in BUILD_SPEC.md section 4 are therefore still unanswered — see
[Blocked on account balance](#blocked-on-account-balance). Two real bugs in the
probe were found and fixed on the way there, both Windows-specific, both
recorded under [Platform bugs found and fixed](#platform-bugs-found-and-fixed).

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

**All five are unanswered: no audio has been transcribed yet.** The probe is
built, tested and verified against the live endpoint as far as the config
frame; it is the account balance, not the tooling, that is missing. Each answer
below will be filled in with quoted token excerpts from the JSONL, as the spec
requires.

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

**This is the one question the podcast audio may not be able to answer.** The
scripted sentence in the heading was never recorded, and a 5-minute cut of
conversational Arabic is not guaranteed to contain any English at all. If the
capture shows no mixed-script segment, question 3 stays open and a different
range has to be cut from `podcast-full.m4a` - the analyzer already prints
"_No mixed-script segment in this capture._" for exactly this case, so the
answer will be unambiguous either way.

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

## The test audio

**Not the scripted sentences.** The ten-sentence script was never recorded.
What is on the machine instead:

| Path | What it is |
| --- | --- |
| `recordings/podcast-5min.m4a` | a 5-minute cut, 10:00-15:00, of a real Arabic podcast |
| `recordings/podcast-full.m4a` | the full episode that cut came from |

Natural conversational speech, several speakers, not read aloud - which makes
it a *better* probe subject than the script for questions 1, 2, 4 and 5, and a
worse one for question 3, since nobody controls how much English it contains.

Two consequences for how its output must be read:

- **There is no reference transcript.** Hamza does not speak Arabic, so neither
  accuracy nor dialect can be checked against a known-good source. Any verdict
  on transcript quality from this audio is an impression, not a measurement.
  The side-by-side table against the ten sentences is **dropped** - there is
  nothing to put in the "what I said" column.
- **Whether it answers question 3 at all is itself an open question**, to be
  decided from the capture: if the cut carries little or no English, a
  different timestamp range has to be cut from `podcast-full.m4a`.

### Provenance: this audio must never be committed

The recording is a cut from a YouTube video - third-party copyrighted material,
and personal speech that nobody consented to have redistributed.

- `recordings/` is gitignored (`.gitignore:6`); both files confirmed ignored
  via `git check-ignore`.
- **No audio goes into `tests/fixtures/`**, ever - not the m4a, not a decoded
  wav, not a trimmed excerpt. Fixtures are the **JSONL captures only**, which
  carry text and timings rather than the recording itself.
- That still makes any committed capture from this source a verbatim
  transcript of someone's podcast. Fine for a private repo's test data; worth
  a second thought before this repo is ever made public.

## Blocked on account balance

The one thing standing between this document and its answers.

```json
{"tokens": [], "final_audio_proc_ms": 0, "total_audio_proc_ms": 0,
 "error_code": 402, "error_type": "organization_balance_exhausted",
 "error_message": "Organization balance exhausted. Please either add funds manually or enable autopay.",
 "more_info": "https://soniox.com/docs/api-reference/errors#organization-balance-exhausted",
 "request_id": "e819e5d6-15ff-4b91-9ae1-9928358efbca"}
```

Reproduced on four separate connections (request ids `7102f9c0`, `3db05f98`,
`1a9f7e6b`, `e819e5d6`), arriving roughly 180 ms after the config frame, every
time before a single token.

**What this already proves**, without transcribing anything:

- The `Authorization: Bearer` header works. A bad key returns
  `unauthenticated`; this is a billing refusal, so the credential is good.
- The config frame is accepted - including `translation: {"type": "two_way",
  "language_a": "ar", "language_b": "en"}`, `enable_endpoint_detection`, and
  the full `context` object with Arabic in `translation_terms`. A malformed
  config returns `invalid_request` instead.
- Phase 5's classification of `organization_balance_exhausted` as **fatal, do
  not retry** is right, and the probe honored it: one attempt, no backoff loop.

**To unblock:** add funds or enable autopay in the Soniox console, then run
`docs/captures/RUN.md` top to bottom. While in the console, set the monthly
budget cap (BUILD_SPEC section 8) - budget-exhausted errors are handled as
fatal, so a cap fails safe rather than running up a bill.

### One live detail the spec does not mention

An error response is **a normal response object with error fields added**, not
a bare error document: it carries `tokens: []`, `final_audio_proc_ms` and
`total_audio_proc_ms` alongside `error_type`, plus an undocumented `more_info`
URL. BUILD_SPEC section 3 describes errors as JSON with
`error_code`/`error_type`/`error_message`/`request_id`, which is true but
incomplete.

Why it matters for Phase 5: a reader that checks for `tokens` before checking
for `error_type` will treat a fatal error as a successful response that
happened to contain no tokens, and sail straight past it. Check `error_type`
first. `tools/probe_soniox.py` already does.

## Platform bugs found and fixed

Both were hit while trying to get the first capture out of a Windows machine,
and both would have struck again on run day. Neither is a Soniox issue.

### 1. Arabic output crashed the probe whenever stdout was redirected

`UnicodeEncodeError: 'charmap' codec can't encode characters in position
553-559` - thrown by the `config:` echo, because `config/glossary.yaml` carries
Arabic in `translation_terms`.

Python picks the locale encoding (cp1252 here) for a redirected stdout, so the
first Arabic character killed the run. Not cosmetic: it would have taken down
**every** piped or logged run the moment an Arabic caption rendered, which is
to say all of them.

Fixed by `force_utf8_output()`, called as the first statement in `main()`,
reconfiguring stdout and stderr to UTF-8 with `errors="replace"`.

### 2. The probe hung after reporting an error, and leaked ffmpeg

An early-ending stream - the balance error, or any dropped connection - left
the probe wedged for minutes after printing the error, then buried it under a
`RuntimeError: Event loop is closed` traceback. Three stacked causes:

1. **`proc.wait()` never returns while a pipe transport is still open.** ffmpeg
   sits mid-file with a full stdout pipe, `terminate()` returns, `returncode`
   stays `None`, and `wait()` blocks forever. Draining stdout first is what
   lets the process be reaped - measured: `wait()` returns in ~10 ms after a
   drain, versus never without one. This is the one worth remembering; it will
   bite `FileSource` in Phases 2-3 and 5, and the Phase 7 eval tools, every
   time a stream ends early.
2. **The sender kept streaming into a dead connection.** The receiver recorded
   the error immediately, but the send loop went on pacing audio in real time,
   so a rejected stream still took the recording's full duration to fail. The
   two now race, and the sender is cancelled when the server ends the stream
   first: ~2 s to fail instead of ~5 min.
3. **ffmpeg was never shut down on the error path at all**, so its transport
   was garbage-collected after the loop closed, printing the traceback that hid
   the actual error message.

`stop_ffmpeg()` now runs in a `finally`, drains both pipes before reaping, and
bounds every step so a wedged decoder cannot outlive the probe.
`tests/test_probe_shutdown.py` (6 tests) locks the drain-before-reap ordering
and the timeouts; removing either guard fails them.

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
