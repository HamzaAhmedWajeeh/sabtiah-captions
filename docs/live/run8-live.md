# run8 — the fixes confirmed (2026-10-13)

The short re-run after run7. One minute of English with `agent` and `ChatGPT`,
a deliberate Wi-Fi drop mid-sentence, then an Arabic passage. **Everything
run7 broke now works**, and the run produced the project's first raw
token-level log.

| | |
| --- | --- |
| Source | `Microsoft Sound Mapper - Input @ 16000 Hz` (device 0) |
| Length | 103.7 s, **88.6 s billable** |
| Responses | 323, 2,221 tokens |
| Outage | one gap of **27.3 s** in the response stream |
| Audio dropped | **18.6 s** (27.3 s outage less the 10 s replay window) |
| Raw log | `logs/live-2026-10-13-132705.jsonl` — 323 responses, replayable |
| Spend after | **27.7 min** of the $10 budget |

## The three fixes, confirmed

What the page actually showed (`run8-live.json`, read back out of the server):

```
en  2. An agent is a program that uses tools to finish a task, most people
       first saw this idea with ChatGPT and now every company is building an
       agent for their own work
en  3. [connection interrupted]
en  4. Like it should work perfectly regardless of whether the internet is on
       or off.

ar  2. الوكيل هو برنامج يستخدم أدوات لإتمام مهمة؛ ... مع ChatGPT، والآن كل شركة تبني
ar  3. [انقطع الاتصال]
ar  4. يبدو أنه ينبغي أن يعمل بشكل مثالي بغضّ النظر عمّا إذا كان الإنترنت قيد التشغيل أو مُغلقًا.
```

1. **The notice appears in both streams**, in the right place, styled as an
   aside. Hamza confirmed it on the phone.
2. **No glued words.** Paragraph 2 ends cleanly and paragraph 4 starts
   cleanly, where run7 produced `behindMicrosoft`.
3. **Captions resumed live, with no lag** — the replay cap did its job.

### A detail worth keeping

Replaying the raw capture through a fresh `CaptionState` gives a *different*
result from what the room saw:

```
replayed:  ...now every company is buildingLike it should work perfectly...
live:      ...now every company is building an agent for their own work
           [connection interrupted]
           Like it should work perfectly...
```

Two things cause that, and both are correct:

- **A capture records Soniox responses, not connection events.** A replay has
  no drop to react to, so `mark_gap` and `add_notice` never fire and the two
  sentences glue exactly as they did on run7. The capture is a faithful record
  of what Soniox sent and **not** of what the page rendered.
- **The live session has more text**: `an agent for their own work` was an
  uncommitted partial when the connection died, and `mark_gap` committed it.
  The replay drops it, because a partial is only ever cleared by the next
  response and there was none.

So a capture is a good fixture for caption logic and a poor one for
reconnect behaviour. Noted as an open item.

## Terms

| term | English | Arabic caption | verdict |
| --- | --- | --- | --- |
| `agent` (singular) | "An agent is a program..." | **الوكيل** | translated, but **correct** — "the agent". Accepted. |
| `ChatGPT` | "...first saw this idea with ChatGPT" | **ChatGPT** | kept English ✅ |

`agent` behaves like `agents` did — `translation_terms` loses to a settled
Arabic equivalent — but unlike run7's `أجهزة` ("devices") the result is right.
The limitation stands; the symptom is milder than feared.

## The Arabic-in-English question: not reproduced

run7 put one Arabic phrase in the English stream and there was no token-level
record to explain it. run8 had the record, and had the conditions:

- **723 original Arabic tokens**, and a deliberate mid-run switch from English
  to Arabic — the same shape of event suspected on run7.
- **0 tokens tagged `language: "en"` containing Arabic script.**
- **0 original tokens with no `language` field**, so the one fallback in
  `captions.py` that could misroute did not fire here either.

That does not prove run7's cause, but it rules out anything systematic: under
the same conditions, with a language switch in the middle, nothing leaked. The
reasoning in `docs/soniox-notes.md` stands, and the instrument now exists — if
it recurs, one grep over the raw log answers it.

## Saudi dialect

The Arabic passage is again **Levantine**, not Saudi — `شو طموح الشركة؟`,
`مشان نعرّف`, `بفترة قليلة`. Same clip family as run7. The open item is
unchanged: the audience is Saudi and no Saudi dialect audio has been measured.

`هيمين` for the company name is the same proper-noun failure as run7, and
`config/session.yaml` is still empty.
