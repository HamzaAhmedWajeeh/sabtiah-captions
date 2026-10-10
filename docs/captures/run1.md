# Soniox probe analysis: `run1.jsonl`

- audio: `recordings\podcast-5min.m4a`
- responses: 1664
- endpoint-bounded segments: 30

## Q1. Translation tokens arriving after the segment's `<end>`

| segment | translation tokens | arrived after `<end>` | last one (ms after `<end>`) |
| --- | --- | --- | --- |
| 0 | 156 | 0 | 0 |
| 1 | 4 | 0 | 0 |
| 2 | 11 | 0 | 0 |
| 3 | 242 | 0 | 0 |
| 4 | 15 | 0 | 0 |
| 5 | 7 | 0 | 0 |
| 6 | 16 | 0 | 0 |
| 7 | 7 | 0 | 0 |
| 8 | 33 | 0 | 0 |
| 9 | 87 | 0 | 0 |
| 10 | 2 | 0 | 0 |
| 11 | 25 | 0 | 0 |
| 12 | 8 | 0 | 0 |
| 13 | 71 | 0 | 0 |
| 14 | 11 | 0 | 0 |
| 15 | 15 | 0 | 0 |
| 16 | 2 | 0 | 0 |
| 17 | 7 | 0 | 0 |
| 18 | 256 | 0 | 0 |
| 19 | 9 | 0 | 0 |
| 20 | 2 | 0 | 0 |
| 21 | 2 | 0 | 0 |
| 22 | 2 | 0 | 0 |
| 24 | 16 | 0 | 0 |
| 25 | 337 | 0 | 0 |
| 26 | 8 | 0 | 0 |
| 27 | 124 | 0 | 0 |
| 28 | 21 | 0 | 0 |
| 29 | 8 | 0 | 0 |

Segments whose translation finished only after `<end>`: **0 of 29**.

## Q2. Is there an end marker on the translation side?

`<end>` tokens carrying `translation_status: "translation"`: **0**.

## Q3. Token languages in mixed sentences

Segments whose original text mixes Arabic and Latin script: **13 of 30**.

**Segment 0** — original: `فعلاً معمولة من البروتينات، بالنسبة لأي حد عايز يفهم علم البيولوجي، عايز يقدر يتخيل شكل البروتين ده إيه. لكن البروتين فولدينج دي كانت مشكلة بتاخد واحد مثلاً بيدرس بيولوجي على درجة الدكتوراه، بتاخد تيم مع بعضه ياخد مثلاً 4 سنين عشان to fold one protein. فلما ظهر ألفا فولد، ألفا فولد وقتها كان يمكن مثلاً فيه 40,000 بروتين مفهوم للعالم، ألفا فولد استنتج الشكل 200 مليون بروتين.`

- token languages: {'ar': 180}
- Latin-script tokens and their `language`: `to`=ar, `f`=ar, `old`=ar, `one`=ar, `prote`=ar, `in.`=ar
- translation: `It's actually made from proteins, for anyone who wants to understand biology, and wants to be able to imagine what this protein looks like. But folding protein was a problem that would take someone, for example, a biology student in a PhD degree, and it would take a team together to spend, say, 4 years to fold one protein. So when AlphaFold appeared—AlphaFold at the time was maybe, for example, there were 40,000 proteins understood by the world—AlphaFold inferred the shape of 200 million proteins.`
- translation keeps Latin script: True

**Segment 3** — original: `الأرض، وكمان ابتدى يتخيل بروتينات جديدة ممكن نعمل منها أجهزة جديدة، بيولوجية. تمام؟ فبذكاء اصطناعي في مجرد إن أنت دربت الذكاء الاصطناعي على وقتها البروتينز اللي موجودة، قلت له بقية البروتينز هتتشكل إزاي، راح مشكلها كلها. فده كان في رأيي أنا تغيير واضح وصريح للعالم. ليه؟ لأن أنا النهارده لما يجي لي فيروس، الفيروس بيتعامل مع جسمي إزاي؟ إن هو بيتعامل مع البروتينات دي، فأنا لو مش فاهم شكل البروتين وعندي شكل ثري دايمنشنال ليه أقدر أشوف أنا هركب إيه على البروتين ده عشان يعالج ده وهكذا. من 2016 برضه كان جيفري هينتونز زميل عزيز في جوجل، جيفري أعتقد في رأيي أنا هو اللي يعني بيسموه جد الـ AI.`

- token languages: {'ar': 289}
- Latin-script tokens and their `language`: `A`=ar, `I.`=ar
- translation: `On Earth, and it also started to imagine new proteins that we could make new devices from—biological ones. Alright? So with artificial intelligence, just by training the proteins that exist at the time, you tell it how the rest of the proteins will form, and it forms them all. So in my opinion, that was a clear and straightforward change for the world. Why? Because today, when a virus hits me, how does the virus interact with my body? It interacts with these proteins, so if I don't understand the protein's shape and have a three-dimensional view, how can I see what I'm going to put on that protein so it can treat it, and so on. Since 2016 as well, Geoffrey Hinton, a dear colleague, at Google—Geoffrey, I think in my opinion, is the one they call the grandfather of AI.`
- translation keeps Latin script: True

**Segment 4** — original: `كنت هقول أبو الـ AI، آه.`

- token languages: {'ar': 15}
- Latin-script tokens and their `language`: `A`=ar, `I`=ar
- translation: `I was going to say the father of AI, yeah.`
- translation keeps Latin script: True

**Segment 5** — original: `أو أبو الـ AI.`

- token languages: {'ar': 7}
- Latin-script tokens and their `language`: `A`=ar, `I.`=ar
- translation: `Or the father of AI.`
- translation keeps Latin script: True

**Segment 7** — original: `بس الـ audience بتوعنا بس.`

- token languages: {'ar': 13}
- Latin-script tokens and their `language`: `aud`=ar, `ien`=ar, `ce`=ar
- translation: `But only our audience.`
- translation keeps Latin script: True

**Segment 9** — original: `لا، هو جيفري. جيفري الحقيقة يعني في رأيي أنا معجزة، ليه؟ لأن هو أولاً اشتغل في الـ AI في سنين كتير جداً، بس هو كان دايماً بيوضح لنا حاجات بسيطة جداً. اللي هو بعد عارف، عارف لما تبقى قاعد مع حد وبعدين يقول لك.`

- token languages: {'ar': 106}
- Latin-script tokens and their `language`: `A`=ar, `I`=ar
- translation: `No, it's Geoffrey. Geoffrey, honestly, in my opinion, is a miracle, why? Because first, he worked in AI for many very many years, but he was always showing us very simple things. Like, you know—when you're sitting with someone and then he tells you.`
- translation keeps Latin script: True

Per-token `language` distribution across all original final tokens:

```
{'ar': 1779}
```

## Q4. `source_language` on translation tokens

| source_language | language | tokens |
| --- | --- | --- |
| `ar` | `en` | 1504 |

## Q5. Delay from a word's `end_ms` to its translation arriving

Measured against the real-time send clock (`recv_offset_ms - end_ms`), so this is end-to-end wall clock.

- original final token after the word was spoken: median **1510 ms**, p90 3276 ms, min 541, max 6244 (n=1779)
- first translation token after the segment's last word: median **601 ms**, p90 782 ms, min -56713, max 879 (n=29)
- last translation token after the segment's last word: median **674 ms**, p90 789 ms, min 584, max 914 (n=29)

## Sample segments

- **0** `فعلاً معمولة من البروتينات، بالنسبة لأي حد عايز يفهم علم البيولوجي، عايز يقدر يتخيل شكل البروتين ده إيه. لكن البروتين فولدينج دي كانت مشكلة بتاخد واحد مثلاً بيدرس بيولوجي على درجة الدكتوراه، بتاخد تيم مع بعضه ياخد مثلاً 4 سنين عشان to fold one protein. فلما ظهر ألفا فولد، ألفا فولد وقتها كان يمكن مثلاً فيه 40,000 بروتين مفهوم للعالم، ألفا فولد استنتج الشكل 200 مليون بروتين.`
  - → `It's actually made from proteins, for anyone who wants to understand biology, and wants to be able to imagine what this protein looks like. But folding protein was a problem that would take someone, for example, a biology student in a PhD degree, and it would take a team together to spend, say, 4 years to fold one protein. So when AlphaFold appeared—AlphaFold at the time was maybe, for example, there were 40,000 proteins understood by the world—AlphaFold inferred the shape of 200 million proteins.`
- **1** `يا نهار أبيض!`
  - → `Oh my God!`
- **2** `كويس؟ كل بروتين معروف على.`
  - → `Okay? Every protein is known to...`
- **3** `الأرض، وكمان ابتدى يتخيل بروتينات جديدة ممكن نعمل منها أجهزة جديدة، بيولوجية. تمام؟ فبذكاء اصطناعي في مجرد إن أنت دربت الذكاء الاصطناعي على وقتها البروتينز اللي موجودة، قلت له بقية البروتينز هتتشكل إزاي، راح مشكلها كلها. فده كان في رأيي أنا تغيير واضح وصريح للعالم. ليه؟ لأن أنا النهارده لما يجي لي فيروس، الفيروس بيتعامل مع جسمي إزاي؟ إن هو بيتعامل مع البروتينات دي، فأنا لو مش فاهم شكل البروتين وعندي شكل ثري دايمنشنال ليه أقدر أشوف أنا هركب إيه على البروتين ده عشان يعالج ده وهكذا. من 2016 برضه كان جيفري هينتونز زميل عزيز في جوجل، جيفري أعتقد في رأيي أنا هو اللي يعني بيسموه جد الـ AI.`
  - → `On Earth, and it also started to imagine new proteins that we could make new devices from—biological ones. Alright? So with artificial intelligence, just by training the proteins that exist at the time, you tell it how the rest of the proteins will form, and it forms them all. So in my opinion, that was a clear and straightforward change for the world. Why? Because today, when a virus hits me, how does the virus interact with my body? It interacts with these proteins, so if I don't understand the protein's shape and have a three-dimensional view, how can I see what I'm going to put on that protein so it can treat it, and so on. Since 2016 as well, Geoffrey Hinton, a dear colleague, at Google—Geoffrey, I think in my opinion, is the one they call the grandfather of AI.`
- **4** `كنت هقول أبو الـ AI، آه.`
  - → `I was going to say the father of AI, yeah.`
- **5** `أو أبو الـ AI.`
  - → `Or the father of AI.`
- **6** `في رأيي أنا إن ده حقيقي، إنه جيفري.`
  - → `In my opinion, it's true—it's Geoffrey.`
- **7** `بس الـ audience بتوعنا بس.`
  - → `But only our audience.`
- **8** `ده زي ما أنت بتقول يعتبر أبو. فده زميل جودت بس عشان الناس تبقى عارفة.`
  - → `That, like you're saying, is considered the father. So this is just a colleague of Goudat so people know.`
- **9** `لا، هو جيفري. جيفري الحقيقة يعني في رأيي أنا معجزة، ليه؟ لأن هو أولاً اشتغل في الـ AI في سنين كتير جداً، بس هو كان دايماً بيوضح لنا حاجات بسيطة جداً. اللي هو بعد عارف، عارف لما تبقى قاعد مع حد وبعدين يقول لك.`
  - → `No, it's Geoffrey. Geoffrey, honestly, in my opinion, is a miracle, why? Because first, he worked in AI for many very many years, but he was always showing us very simple things. Like, you know—when you're sitting with someone and then he tells you.`

