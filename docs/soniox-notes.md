# Soniox notes (Phase 1)

Status: **answered.** Three 5-minute captures against `stt-rt-v5` on
2026-10-11, all five questions in BUILD_SPEC.md section 4 settled with token
evidence below. One gap remains and it is a big one: **the English-to-Arabic
direction was never exercised**, because the test audio contains no English
speech. See [What is still untested](#what-is-still-untested).

| Capture | Config | Responses | Segments |
| --- | --- | --- | --- |
| `run1.jsonl` | two-way `ar`/`en`, glossary context | 1664 | 30 |
| `run2-oneway.jsonl` | one-way to `en`, glossary context | 1639 | 28 |
| `run3-noglossary.jsonl` | two-way `ar`/`en`, empty context | 1672 | 31 |

All three streamed the full 300 s and closed with `{"finished": true}`. Audio
spend so far: **15 minutes**.

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
misattributed to the newer segment - and because the heuristic would then
*hide* the lateness, it cannot be used to prove that lateness never happens.
Question 1 was therefore settled with a second, attribution-free check over the
raw token order; both agree that no translation ever arrives late. The
heuristic's one visible artefact is in the analyzer's Q5 "first translation
token" row, which reports `min -56713 ms`: for a 59 s segment the first
translation token legitimately arrives a minute before the segment's *last*
word, so a large negative value there is correct, not a bug. The "last
translation token" row is the one to read.

## The five answers

Every number below is from `run1.jsonl` unless stated. `run2-oneway.jsonl`
agrees on all five.

### 1. Do translation tokens for a segment ever arrive after that segment's `<end>`? How often, how late?

**No. Never, in either capture.** This was the question that gated the caption
design, and it came back clean.

The analyzer reports 0 late translations across all 29 segments that had both
an `<end>` and translation tokens. That result leans on the analyzer's
"attach to the newest segment with original text" heuristic, which can *hide* a
late translation, so it was re-checked a second way that needs no attribution
at all: walk the raw ordered stream of final tokens and ask, after each
original `<end>`, whether any translation token arrives before the next
original token. A trailing translation could only land in that window.

```
original <end> markers: 30
those followed by translation tokens before the next original token: 0
original <end> responses that also carried translation tokens: 0 of 30
```

A third check confirms the translated side is not quietly falling behind over
five minutes: the ratio of translated characters to original characters
delivered, sampled at every `<end>`, holds flat rather than drifting.

```
recv=  36721ms  orig=  376  tr=  502  tr/orig=1.34
recv=  89839ms  orig= 1007  tr= 1326  tr/orig=1.32
recv= 120438ms  orig= 1447  tr= 1888  tr/orig=1.30
... last <end>: orig=3700 tr=4602 ratio=1.24
```

So translation is complete before the endpoint fires, and `<end>` is a safe
point at which to consider a segment's translation finished. **This is the
evidence that makes Phase 2 Option A technically available** — though see the
recommendation, which does not take it.

### 2. Is there any end marker on the translation side?

**No.** Zero `<end>` tokens carry `translation_status: "translation"`, in all
captures. The translated stream has no native paragraph boundary.

What `<end>` *does* carry is a surprise worth guarding against:

```json
{"text": "<end>", "is_final": true, "confidence": 1, "translation_status": "none"}
```

`translation_status` is **`"none"`**, not `"original"`, and the token has no
`language`, no `start_ms` and no `end_ms`. A Phase 2 reader that builds the
original stream by filtering `translation_status == "original"` would silently
discard every endpoint marker and never break a paragraph. Treat
"not `translation`" as original, which is what `build_segments` does.

### 3. In a mixed sentence, what `language` do original tokens carry? Per token or per segment? Does the translation keep English in English?

**`language` is never per-token. It is `"ar"` on every single original token**,
including the ones that are pure English, across all 12,957 original tokens in
run1 and 14,954 in run2. Not one token came back `"en"`.

```
original tokens by language:            {'ar': 12957, None: 30}
original Latin-containing by language:  {'ar': 531, None: 30}
```

(The `None` group is the 30 `<end>` markers, which carry no `language` at all.)

Evidence at the token level, from segment 0 — the speaker says "to fold one
protein" mid-Arabic-sentence, and every piece of it is tagged `ar`:

```
`to`=ar, `f`=ar, `old`=ar, `one`=ar, `prote`=ar, `in.`=ar
```

Segment 24's original is `Small primitive AIs، يعني لسه AIs مبدئية.` — note the
Arabic comma `،` directly after Latin text — and its tokens are `ar` too.

**The transcript keeps English in English, and so does the translation.** 13 of
30 segments mix scripts. English terms the speakers actually used came back
intact and correctly cased: `AI`, `AIs`, `reinforcement learning`,
`transformers`, `The Maker`, `The Teacher`, `Student`, `agents`, `bias`,
`hallucinations`, `Small primitive AIs`, `to fold one protein`, `audience`.
Segment 24 translates to `Meaning small primitive AIs, still basic AIs.` —
Latin preserved on both sides.

Two consequences for Phase 2, and they pull in opposite directions:

- **Good:** the spec's worry that "English words inside an Arabic sentence leak
  into the English stream as fragments" cannot happen via per-token language,
  because Soniox never tags them `en`. Option B's "assign the segment to its
  majority language" rule is **unnecessary** — Soniox has already done it.
- **Bad:** there is no per-token language signal to use for anything else, and
  `enable_language_identification: true` bought nothing on this audio. Routing
  must key off `translation_status` (original vs translation) and the config's
  known direction, never off `language`.

Some English the speakers used came back **transliterated into Arabic script**
rather than as Latin text — `فولدينج` (folding), `بروتينز` (proteins),
`دايمنشنال` (dimensional), `داتا` (data), `تيم` (team), `إنترنت` (internet),
`تكنولوجي` (technology), and `ألفا فولد` (AlphaFold, 3x). Soniox is
transcribing what it hears, and these were said with Arabic phonology. It is
not wrong, but it means a glossary cannot be relied on to force a term into
Latin script — see the glossary section.

### 4. What does `source_language` contain on translation tokens?

**The source language code, consistently.** Every translation token in every
capture is the same pair:

| capture | source_language | language | tokens |
| --- | --- | --- | --- |
| run1 (two-way) | `ar` | `en` | 1504 |
| run2 (one-way) | `ar` | `en` | 1481 |

So `source_language` is usable to tell translation direction, which is what
Phase 2 needs for routing into the `ar`/`en` streams. The caveat is that this
audio only ever produced `ar` → `en`; the reverse pair was never observed
because nobody spoke English (see [What is still
untested](#what-is-still-untested)).

Translation tokens also carry a **different field set** from original tokens —
no timestamps, exactly as the docs said:

```
ORIGINAL:    confidence, end_ms, is_final, language, start_ms, text, translation_status
TRANSLATION: confidence, is_final, language, source_language, text, translation_status
```

### 5. Typical delay between a word's `end_ms` and its translation token arriving?

Audio is paced at real time, so `recv_offset_ms - end_ms` is true end-to-end
wall clock. Each distinct token is counted once — non-finals are re-sent in
full on every response, and averaging over every occurrence inflates the lag
badly (it reports 1242 ms instead of 639 ms).

| What the viewer sees | median | p90 | p99 | max |
| --- | --- | --- | --- | --- |
| original word first appears (non-final) | **639 ms** | 701 | 767 | 1013 |
| original word settles (final) | **1510 ms** | 3276 | 5224 | 6244 |
| translation arrives (vs audio frontier) | **679 ms** | 753 | 854 | 879 |

Translation tokens have no timestamps, so their lag is measured against the
audio frontier: the newest `end_ms` any original token has reported when the
translation arrives.

Three things worth internalising:

1. **Both languages are on screen in about 0.65 s.** The translated caption is
   not meaningfully slower than the original one — 679 ms vs 639 ms. This is
   much better than the spec assumed.
2. **Finalisation is the slow, jittery part**: median 1.5 s but p99 5.2 s and
   max 6.2 s. Text that has already appeared keeps being revised for seconds.
   The UI must show non-final text; waiting for finals would feel broken.
3. **Translation tokens are always final.** 1504 of 1504 in run1, 1481 of 1481
   in run2 — `is_final: true`, every one. The translated stream is append-only
   and has **no partial text at all**, while the original stream is mostly
   partials (11,178 non-final vs 1,779 final). That asymmetry is a Phase 2
   design input: the Arabic view gets live typing, the English view gets
   finished chunks arriving every ~0.7 s.

## The test audio

**Not the scripted sentences.** The ten-sentence script was never recorded.
What is on the machine instead:

| Path | What it is |
| --- | --- |
| `recordings/podcast-5min.m4a` | a 5-minute cut, 10:00-15:00, of a real Arabic podcast |
| `recordings/podcast-full.m4a` | the full episode that cut came from |

Natural conversational speech, two speakers who interrupt each other, not read
aloud - which made it a *better* probe subject than the script would have been
for questions 1, 2, 4 and 5, because the uneven pauses exercise endpoint
detection far harder than scripted sentences with tidy 1-2 s gaps.

Two consequences for how its output must be read:

- **There is no reference transcript.** Hamza does not speak Arabic, so neither
  accuracy nor dialect can be checked against a known-good source. Any verdict
  on transcript quality from this audio is an impression, not a measurement.
  The side-by-side table against the ten sentences is **dropped** - there is
  nothing to put in the "what I said" column.
- **It did turn out to carry enough English to answer question 3**: 32
  Latin-script runs, 17 distinct, across 13 of 30 segments. No re-cut of
  `podcast-full.m4a` is needed for that question. What it carries is English
  *terms inside Arabic sentences*, though - never a turn spoken in English - so
  it cannot test the `en`->`ar` direction. See
  [What is still untested](#what-is-still-untested).

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

## The runs

Balance was topped up on 2026-10-11 and all three passes completed.

One transient failure on the first attempt, worth knowing because it will
happen again on run day:

```
TimeoutError: timed out during opening handshake
```

It failed in 10 s, left nothing behind, and the immediate retry connected
normally. `soniox.com` answered HTTP 200 in 1.8 s either side of it, so this
was the WebSocket upgrade, not the network being down. Phase 5 should treat an
opening-handshake timeout as **retryable with backoff** — it is not in the
documented `error_type` list because it never reaches the point of getting a
response.

The earlier `organization_balance_exhausted` (HTTP 402) finding still stands as
a record of how a fatal error behaves: reproduced on four connections, arriving
~180 ms after the config frame, before any token. The probe did not retry it,
which is the correct handling.

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

## What dialect this is

**Egyptian Arabic, with high confidence. Not Saudi.** This matters because the
demo audience is Saudi, so the dialect the system will actually face on
Saturday is still untested.

The judgment is from dialect markers in the transcript, not from the audio:

| Marker | Count | Gloss | Saudi equivalent |
| --- | --- | --- | --- |
| `ده` / `دي` | 20 / 15 | this / this-f | `هذا` / `هذي` |
| `مش` | 9 | not | `ما` / `مو` |
| `بقى` | 5 | so, then | — |
| `عايز` | 3 | want | `أبغى` / `أبي` |
| `إيه` | 3 | what | `وش` |
| `إزاي` | 3 | how | `كيف` |
| `كده` | 3 | like this | `كذا` |
| `النهارده` | 1 | today | `اليوم` |
| `برضه` | 1 | also | — |
| `بتاع` | 1 | of | `حق` |

Gulf/Saudi markers searched for and **not found**: `وش`, `أبغى`, `الحين`,
`وايد`, `شنو`, `زين`, `جذي`, `تبغى`. The one apparent `أبي` hit is a substring
false positive — the transcript discusses `أبو الـ AI` ("the father of AI").

`عايز`, `إزاي`, `النهارده` and `بقى` together are close to decisive: that
cluster is Egyptian and is not used in Gulf speech. Soniox transcribed it
confidently (mean confidence 0.973 on final original tokens, median 1.000),
which says the model handles Egyptian well. It says nothing about Saudi.

## Is the English translation coherent?

Yes — markedly better than expected for conversational, interrupted,
code-switched speech. Judge for yourself; these are verbatim from `run1.jsonl`.

**The three best.** Long, technical, and they hold together:

> It's actually made from proteins, for anyone who wants to understand biology,
> and wants to be able to imagine what this protein looks like. But folding
> protein was a problem that would take someone, for example, a biology student
> in a PhD degree, and it would take a team together to spend, say, 4 years to
> fold one protein. So when AlphaFold appeared—AlphaFold at the time was maybe,
> for example, there were 40,000 proteins understood by the world—AlphaFold
> inferred the shape of 200 million proteins.

> Then why didn't I think about that? It's very clear right after he says it. So
> in 2016 he started talking about something called reinforcement learning, and
> he talked about Transformers and the technology we know today.

> And from 2016, life started advancing at a very high pace, and in 2023 one of
> the examples appeared it was ChatGPT, but ChatGPT, as you could say, broke the
> agreement between us. Why? Because we were all saying that we create miracles
> with AI, but we didn't know whether this AI was at a level of safety for
> humanity that would allow us to put it on the internet.

Technical content survives: `AlphaFold`, `reinforcement learning`,
`Transformers`, `ChatGPT`, `200 million proteins`, `40,000`, `2016`, `2023`.
Numbers and years were right every time they appeared.

**The two worst.** Both are failures of *speech*, not of translation:

> That, like you're saying, is considered the father. So this is just a
> colleague of Goudat so people know.

`جودت` is the surname Gawdat, rendered `Goudat`. Proper nouns are the weak
spot, and this is exactly what `config/session.yaml`'s `speakers` list exists
to fix — it was empty for these runs.

> You say, "Okay, come over there you'll take another year." And the one who
> says "no, 6, kill it." Okay? That's exactly the way we wrote the early days of
> deep learning and neural networks.

"kill it" is a literal rendering of discarding a failed model. Accurate,
jarring, and the kind of thing an audience notices.

Two more artefacts worth seeing, because they are structural rather than
linguistic:

> Okay? Every protein is known to...

Segment 2 ends mid-phrase and segment 3 opens with `On Earth,` — the endpoint
fired inside "every protein known on Earth". Captions will break mid-sentence
and there is nothing to be done about it at the engine level.

> Tea.

Segment 10's entire content. A filler syllable became a word. Short segments
are where the noise lives.

## Did the glossary make a visible difference?

**Yes, but barely, and on one word.** The honest answer is that this audio
could not give the glossary much to do: of the twelve terms in
`config/glossary.yaml`, the speakers used exactly **one** — `agents`.

On that one word the glossary earned its place. Same audio, same config, only
`context` differs:

| | original | translation |
| --- | --- | --- |
| run1, glossary on | `اللي هم بوتس أو agents.` | `Which are bots or agents.` |
| run3, glossary off | `اللي هم بوتس أو.` | `Which are bots or...` |

Without the glossary the word was **dropped entirely** — not transliterated,
not garbled, just gone, leaving a dangling "bots or...". With it, the term came
through in Latin script on both sides.

Everything else is noise-level. Whole-transcript similarity between the two
runs is 0.978 on the original and 0.951 on the translation, and the only other
Latin-run differences (`audience`, `AI.`) are not glossary terms and are within
run-to-run variance.

**That variance is the real caveat here.** Soniox is not deterministic: run1
and run2 on identical audio agree on only 0.894 of the original transcript and
0.686 of the translation. With one capture per configuration, the glossary's
effect cannot be cleanly separated from noise — except for the `agents`
observation, which is a direct hit on a glossary term and hard to explain away.

**Recommendation: keep the glossary on.** The cost is a few hundred bytes in
the config frame, the one measurable effect was positive, and the demo's
vocabulary (`RAG`, `MCP`, `LLM`, `embeddings`, `Sabtiah`, `Cycls`) is far
denser in glossary terms than this podcast was. Also fill in
`config/session.yaml` — `speakers` would likely have fixed `Goudat`.

## Two-way or one-way for the demo?

**Two-way, and the margin is not close.** On this audio the two configurations
are near-indistinguishable on every measured axis:

| | run1 two-way | run2 one-way |
| --- | --- | --- |
| responses | 1664 | 1639 |
| segments | 30 | 28 |
| translation tokens | 1504 (`ar`→`en`) | 1481 (`ar`→`en`) |
| late translations | 0 | 0 |
| translation `<end>` markers | 0 | 0 |
| word first appears | 639 ms | 640 ms |
| word settles | 1510 ms | 1717 ms |
| translation arrives | 679 ms | 688 ms |

One-way costs the Arabic audience their captions and buys nothing measurable.
Two-way handled mixed sentences well: English terms stayed English in both the
transcript and the translation, and because every token is tagged `ar`,
embedded English cannot leak into the wrong stream.

**The honest caveat:** "handles mixed sentences well" here means *Arabic speech
containing English terms*. It does not mean *a speaker switching into English
for a whole turn*, which never happened on this audio and is therefore
unmeasured. Keep `config/oneway-en.json` as the Phase 6 fallback — if the
`en`→`ar` direction misbehaves on Saturday, one-way to English is the escape
hatch, and it is one config file away.

## Phase 2 recommendation: Option B

Question 1 passed the gate the spec set for Option A: translations never arrive
after `<end>`, confirmed three ways across three captures. Option A is
genuinely available. I still recommend **Option B**, for four reasons that only
showed up in the data:

1. **`<end>` boundaries are wildly uneven.** Spoken segment length: median
   1.3 s, min 0.1 s, max **59.5 s**. Original text per segment: median 26
   chars, max 732. Eleven of 30 segments are under 20 characters. Option A's
   line-aligned `orig` + `translation` pairs would be sometimes a two-word
   interjection and sometimes a 732-character wall — a bad caption either way.
2. **The two streams have different shapes.** The original side is 11,178
   non-final tokens against 1,779 finals; the translated side has zero
   non-finals. Option A pairs a live, continuously-revised line with a line
   that only ever appends. Option B's independent streams model that honestly.
3. **A segment can have no translation at all.** Segment 23's original is
   `يعني` and its translation is empty. Option A must render a paired line with
   one side blank; Option B just never shows a paragraph there.
4. **The `en`→`ar` direction is untested.** Option A is the harder thing to
   back out of, and six days before the demo is the wrong time to discover its
   precondition only held in one direction.

Option B also needs **less** than the spec assumed: the "assign the segment to
its majority language" rule is unnecessary, because Soniox never tags embedded
English as `en` (question 3). Route on `translation_status` and
`source_language`, never on `language`.

Record for later: Option A's precondition **held**, so if the "both" view
becomes important after the demo, it is a supported change rather than a
gamble.

## What is still untested

The gap that matters most, stated plainly so it does not get lost:

- **English speech → Arabic captions never happened.** Every one of the 4,493
  translation tokens across three captures is `ar`→`en`. The product premise is
  bidirectional; half of it has no evidence behind it. The cheapest fix is a
  60-90 s recording of English speech — which also sidesteps the YouTube
  provenance problem, since it would be first-party audio. That is one run
  inside the 1-5 minute budget rule.
- **Saudi Arabic is untested.** The audio is Egyptian. The audience is Saudi.
- **Live microphone capture is untested.** All three captures came through
  `ffmpeg` from a file. `MicSource` and the `sounddevice` callback path are
  Phase 4.
- **Speaker diarization is off** (`enable_speaker_diarization: false`) and this
  podcast has two speakers who interrupt each other constantly. Several short
  segments are one speaker's interjection landing inside the other's sentence.
  Not a problem for the spec's design, but it is why some captions read oddly.
- **Sessions longer than 5 minutes.** The 300-minute cap and the 280-minute
  rollover (BUILD_SPEC section 3) are unexercised, as is the 20 s keepalive —
  audio flowed continuously, so no keepalive was ever needed.

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
`tests/fixtures/`.

`tests/fixtures/` now holds the two real captures Phase 2 is to be built
against - `run1.jsonl` (two-way) and `run2-oneway.jsonl` - checked to contain
no API key and no audio. `run3-noglossary.jsonl` stays out: it exists to answer
the glossary question, not to drive tests, and a third 2.4 MB fixture earns
nothing.
