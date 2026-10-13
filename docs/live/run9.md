# run9 — the phone does not recover (2026-10-13)

The fourth live run, and the first to find a bug the laptop could not see.
Hamza narrated the pricing finding in English, played the Arabic podcast, then
pulled the laptop's Wi-Fi and put it back.

| | |
| --- | --- |
| Source | `Microsoft Sound Mapper - Input @ 16000 Hz` (device 0) |
| Length | 357.6 s elapsed, **210.5 s billable** (3.51 min) |
| Responses | 789 |
| Outage | one, **38.6 s** (494.5 s → 533.1 s), 20 reconnect attempts |
| Speech lost | **30.7 s** |
| Paragraphs | 18 English, 16 Arabic |
| Raw log | `logs/live-2026-10-13-150917.jsonl` |
| **Cost** | **about 1.1 cents.** Project total 31.2 min ≈ **9.4 cents** |

## The finding: the phone never came back

Hamza turned the laptop's Wi-Fi off and on again. On the laptop the audience
page behaved: it noticed, showed the gap, and carried on. **On the phone it
froze on the last line before the disconnect and never resumed**, even once
the laptop was back online and captioning again.

He narrated it into the transcript, which is the clearest possible bug report:

> what I'm witnessing right now is the mobile is sort of stuck; the mobile
> text shows the last "I am disconnecting the internet," but when I'm checking
> on the laptop version, I can see that the connection interrupted, etc., all
> the flags are coming but not in the phone.

The cause is that **nothing on the viewer socket ever times out**. Killing the
laptop's Wi-Fi drops the phone's TCP connection without either end sending a
FIN, so the phone's WebSocket sits in `OPEN` believing it is connected. No
`onclose` fires, so the page's reconnect logic — which only runs from
`onclose` — is never reached. The laptop page recovered because it is on
loopback, which never went away.

The server's half is the same gap from the other side: it only writes when a
caption changes, so a socket that is silently dead looks exactly like a quiet
moment in the talk.

Both halves are fixed in the commit that follows this write-up: a server
heartbeat every 5 s, a client watchdog that reconnects after 12 s of silence,
and reconnects on the browser's `online` event and on becoming visible again
(a phone waking from sleep).

## The outage itself worked as designed

| | |
| --- | --- |
| Connection lost | 494.5 s, `ConnectionClosedError` |
| Failure during | `gaierror` — DNS, as on run7 |
| Restored | 533.1 s |
| Attempts | 20 |
| Audio dropped | 30.7 s |

38.6 s of outage against a 10 s replay window means ~29 s of speech is
dropped by design, and 30.7 s is what the ledger reports. The
`[connection interrupted]` notice appeared in **both** streams at the right
place (paragraph 13 in each), and the captions resumed near live rather than
30 s behind — which is the decision of 2026-10-13 working exactly as intended.

**20 attempts rather than run7's 7** is the faster network backoff doing its
job: DNS failures now cap at 2 s instead of 8 s, so the client probes more
often and comes back sooner after the radio returns.

## Caption delay

| | normal | including the recovery burst |
| --- | --- | --- |
| median | **1.98 s** | 2.42 s |
| p90 | 6.32 s | 8.58 s |
| max | 10.32 s | 13.30 s |

The "normal" column excludes the 30 s after reconnection, when the replay
window is being pushed out in a burst. Two seconds is a comfortable live
caption; the p90 of 6 s is higher than run7's and worth watching.

## The last words were cut off

The transcript ends:

> Okay, this is Hamza sig

The Arabic stream is two paragraphs shorter than the English, missing the
translation of the last two.

Stop closes the connection immediately. Soniox had final tokens in flight and
a partial that it would have finalized given the chance, and none of it
arrived. Fixed in the same commit: Stop now asks the engine to finish and
waits up to 2 s for the remaining tokens.

## English accuracy

Good, including dense technical narration. The pricing passage came through
almost intact. The errors are what you would expect of speech, not of the
system:

- `RR at 2 dollar per million` and `NR at 4 dollar per million` — "R" and "N"
  are mishearings of the start of a word.
- `The page is 0.12 is this` — garbled.
- `I doubt it wise happening` — probably "I don't know why it's happening".

## Arabic: Levantine again, Saudi still untested

The podcast segment is unmistakably Levantine — `شو طموح الشركة؟`,
`بحكي`, `بلش`, `إحنا`, `كمان شغلة`, `بتيجي مرة بالحياة`. The translation of it
reads well, and the two-way inversion put it in English on the other side.

**Saudi dialect remains unmeasured after four live runs.** The audience is
Saudi. This is now the oldest open item in the project that costs nothing to
close — it needs 30 seconds of casual Saudi speech, nothing more.

## Glossary: not exercised

None of the glossary terms were spoken this run, so it says nothing about
`translation_terms` either way; run8's results stand.

Worth noting anyway: **Latin script survived in the Arabic stream without the
glossary's help** — `Claude`, `ENV`, `NR`, `RR` and `env.example.` all stayed
in Latin. None are in `config/glossary.yaml`. So preservation is not purely
glossary-driven; acronyms and filenames with no settled Arabic form tend to
survive on their own. The glossary's value is in the words that *do* have an
Arabic equivalent the model would otherwise prefer — `transformers` →
`المحوّلات` being the measured case.

No Arabic script leaked into the English stream this run.

## Not tested

- **Apply now.** The mid-talk context change was not exercised.
- **Saudi dialect.** See above.
- **A clean Stop**, since that is what broke.
