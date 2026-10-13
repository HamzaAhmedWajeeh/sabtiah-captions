# run6 — the first live run (2026-10-13)

The first audio this project has streamed since Phase 1. Hamza spoke English
and Arabic through the headset into `/admin` with `source=live`, and read the
captions on his phone.

| | |
| --- | --- |
| Source | `Microsoft Sound Mapper - Input @ 16000 Hz` (device 0) |
| Duration | 137.9 s elapsed, **1.36 min billable** |
| Responses | 221, no errors, no reconnects |
| Paragraphs | 11 English, 10 Arabic |
| Spend after | **23.36 min** of the $10 budget |

The transcript is `run6-first-live.json`, recovered from the server's memory
with `tools/dump_session.py` after the run had finished. **A live run writes no
transcript of its own** — `probe_soniox.py` writes JSONL because it owns its
connection, but `source=live` streams straight into `CaptionState`, and
`session_log.py` is Phase 6. That gap is why the tool now exists.

## The headline: the pipeline works

Both directions produced usable captions on a phone, from a real microphone,
over real Wi-Fi, with no errors and no reconnects. The device negotiated
**native 16 kHz mono**, so no resampling was involved on this path.

## Tech terms: what actually happened

Hamza's report was that `transformers` came back as `المحوّلات`. That is correct,
and `AI` went the same way (`الذكاء الاصطناعي`). **Zero Latin-script runs survive
anywhere in the Arabic stream** — consistent with the Phase 1 finding.

But the conclusion "tech terms are not preserved" needs one correction, and it
matters for what to do next:

| term | spoken? | was in `terms`? | was in `translation_terms`? |
| --- | --- | --- | --- |
| transformers | **yes** | no | no |
| AI | **yes** | no | no |
| RAG | no | yes | yes |
| LLM | no | yes | no |
| MCP | no | yes | no |
| agents | no | yes | no |
| fine-tuning | no | yes | no |
| embeddings | no | yes | no |
| ChatGPT | no | no | no |

**Only two of the nine terms were actually said, and neither was in
`translation_terms`.** `transformers` was not even in `terms`, so it was never
offered to the recogniser either. The context sent on this run was:

```
terms:             RAG, MCP, LLM, LLMs, fine-tuning, agent, agents, agentic,
                   embeddings, vector database, Sabtiah, Cycls
translation_terms: agent->agent, RAG->RAG, Sabtiah->السبتية
general:           []          (config/session.yaml is still empty)
```

So this run says **nothing about whether `translation_terms` works**. The
mechanism meant to preserve those words was never exercised: the three words it
covers (`agent`, `RAG`, `Sabtiah`) were not spoken, and the two words that were
spoken were not covered. `translation_terms` remains exactly as untested as it
was on 2026-10-11 — which is what the next run is for.

## Recognition quality on the Arabic side

The English is clean. The Arabic-to-English direction has real errors worth
knowing about before a room full of Arabic speakers:

- `السلام عليكم` → "Peace be upon you" ✅
- `الشهادة` → "Where is the time of God? Muhammad is the Messenger of God" —
  mangled, and it is a religious formula, so getting it wrong is conspicuous.
- "two ICE signatures, a big cold" — a mis-recognition with no obvious source.
- `الشامية` (Levantine) came out as "Shoya Arabic".

The English stream also shows the two-way inversion working as designed:
Hamza's Arabic speech appears in English there, which is the product.

## What this changes

1. `transformers`, `ChatGPT` and `AI` were missing from `terms` entirely. Added.
2. The nine terms are now `translation_terms` mapped to themselves.
3. `config/session.yaml` is still empty, so `general` was `[]` on a run where
   Hamza said his own name. "This is Hamza" survived, but speaker names are the
   known weak spot (`Goudat`).
