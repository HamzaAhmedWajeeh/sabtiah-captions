# run7 — the terms run and the first real outage (2026-10-13)

The run that answered `translation_terms` and broke the reconnect. Hamza spoke
English with the glossary vocabulary, played a Saudi YouTube clip near the
microphone, and dropped Wi-Fi mid-sentence.

| | |
| --- | --- |
| Source | `Microsoft Sound Mapper - Input @ 16000 Hz` (device 0) |
| Duration | 184.3 s elapsed, **2.88 min billable** |
| Responses | 522 |
| Reconnects | 7 attempts across one outage |
| Paragraphs | 14 English, 14 Arabic |
| Spend after | **26.2 min** of the $10 budget |

Raw transcript and the 2 Hz recording are `run7-live.json` and `run7.jsonl`,
both gitignored — they are verbatim records of the room, and this one includes
a third-party clip played aloud. Everything needed to check the conclusions is
quoted here.

## 1. The glossary worked

Six of six technical terms kept their English form inside the Arabic captions:

> نستخدم **RAG** للإجابة عن الأسئلة من وثائق الشركات، وتُقسَّم الوثائق إلى أجزاء،
> وتُخزَّن كـ **embeddings** في قاعدة بيانات متجهية. ثم يقرأ نظام **LLM** الأجزاء
> التي يتم استرجاعها

> كما جرّبنا أيضًا **fine-tuning** لنموذج صغير، لكن بصراحة **RAG** الجيد يتفوّق
> على الـ **fine-tuning** في حالة استخدامنا. معظم هذه النماذج هي **transformers**

Every Latin-script run in the Arabic stream was a glossary term — `LLM`, `MCP`,
`RAG`, `embeddings`, `fine-tuning`, `transformers` — and nothing else leaked.
The full argument, including the run6/run7 controlled comparison on
`transformers`, is in `docs/soniox-notes.md`.

### The two that did not

> ومؤخرًا كنا نبني **أجهزة** تستخدم MCP لاستدعاء الأدوات.
> *("devices", for "agents")*

> اليوم أريد أن أتحدث عن كيفية بناء أنظمة **الذكاء الاصطناعي** في العمل.
> *(for "AI")*

`vector database` was also translated (`قاعدة بيانات متجهية`), correctly — it is
in `terms` but was never in `translation_terms`.

## 2. The outage

A ~10 s Wi-Fi drop became a 32.5 s outage, lost ~12.5 s of speech, and glued
the text that survived onto the sentence before it:

> Most of these models are transformers, the same architecture
> **behindMicrosoft**, what are they thinking

`the same architecture behind` is the last thing that got through; `ChatGPT`
and about ten seconds after it never arrived. Both bugs are fixed and tested —
the analysis is in `docs/resilience-test.md`.

## 3. The Saudi clip

The clip played into the microphone was transcribed and translated, and the
two-way inversion put it in the English stream:

> We haven't started seeing it break into our lives except in the last few
> years. What is the history of this concept? First, Omar, I want to thank you
> very much for inviting us to the podcast.

The Arabic side kept it in Arabic, and the register is visibly Levantine
rather than MSA — `بدي أشكرك كتير`, `ما بدينا نشوفه`, `شو طموح الشركة؟`. Two
things follow:

- **The dialect handling is better than `run4-saudi` suggested.** That capture
  was MSA news register; this is conversational speech and it came through.
- **It is still not Saudi.** `بدي`/`كتير`/`شو` are Levantine. One proper noun
  came out as `هيمين` ("the company Himin"), which is a recognition failure on
  a name. So the open item stands: the audience is Saudi and no Saudi dialect
  audio has been measured yet.

## 4. The ledger

`logs/usage.jsonl` recorded 173.0 s for this run, against 184.3 s elapsed —
the difference is the 11 s the connection was down and not streaming, which is
the ledger behaving correctly. It does **not** subtract the 21.7 s of replayed
audio, because Soniox bills that too.
