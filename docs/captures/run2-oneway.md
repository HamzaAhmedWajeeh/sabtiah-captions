# Soniox probe analysis: `run2-oneway.jsonl`

- audio: `recordings\podcast-5min.m4a`
- responses: 1639
- endpoint-bounded segments: 28

## Q1. Translation tokens arriving after the segment's `<end>`

| segment | translation tokens | arrived after `<end>` | last one (ms after `<end>`) |
| --- | --- | --- | --- |
| 0 | 156 | 0 | 0 |
| 1 | 4 | 0 | 0 |
| 2 | 11 | 0 | 0 |
| 3 | 239 | 0 | 0 |
| 4 | 15 | 0 | 0 |
| 5 | 7 | 0 | 0 |
| 6 | 18 | 0 | 0 |
| 7 | 7 | 0 | 0 |
| 8 | 33 | 0 | 0 |
| 9 | 85 | 0 | 0 |
| 10 | 2 | 0 | 0 |
| 11 | 26 | 0 | 0 |
| 12 | 10 | 0 | 0 |
| 13 | 68 | 0 | 0 |
| 14 | 11 | 0 | 0 |
| 15 | 15 | 0 | 0 |
| 16 | 2 | 0 | 0 |
| 17 | 7 | 0 | 0 |
| 18 | 90 | 0 | 0 |
| 19 | 149 | 0 | 0 |
| 20 | 9 | 0 | 0 |
| 21 | 4 | 0 | 0 |
| 22 | 2 | 0 | 0 |
| 23 | 348 | 0 | 0 |
| 24 | 8 | 0 | 0 |
| 25 | 126 | 0 | 0 |
| 26 | 21 | 0 | 0 |
| 27 | 8 | 0 | 0 |

Segments whose translation finished only after `<end>`: **0 of 28**.

## Q2. Is there an end marker on the translation side?

`<end>` tokens carrying `translation_status: "translation"`: **0**.

## Q3. Token languages in mixed sentences

Segments whose original text mixes Arabic and Latin script: **8 of 28**.

**Segment 0** — original: `فعلاً معمولة من البروتينات، بالنسبة لأي حد عايز يفهم علم البيولوجي، عايز يقدر يتخيل شكل البروتين ده إيه. لكن البروتين فولدينج دي كانت مشكلة بتاخد واحد مثلاً بيدرس بيولوجي على درجة الدكتوراه، بتاخد تيم مع بعضه ياخد مثلاً 4 سنين عشان to fold one protein. فلما ظهر ألفا فولد، ألفا فولد وقتها كان يمكن مثلاً فيه 40,000 بروتين مفهوم للعالم، ألفا فولد استنتج الشكل 200 مليون بروتين.`

- token languages: {'ar': 180}
- Latin-script tokens and their `language`: `to`=ar, `f`=ar, `old`=ar, `one`=ar, `prote`=ar, `in.`=ar
- translation: `It's actually made from proteins, for anyone who wants to understand biology, and wants to be able to imagine what this protein looks like. But folding protein was a problem that would take someone, for example, a biology student in a PhD degree, and it would take a team together to spend, say, 4 years to fold one protein. So when AlphaFold appeared—AlphaFold at the time was maybe, for example, there were 40,000 proteins understood by the world—AlphaFold inferred the shape of 200 million proteins.`
- translation keeps Latin script: True

**Segment 13** — original: `طب أنا ليه ما فكرتش فيها دي؟ يعني واضحة جداً بعد ما يقولها. فهو 2016 ابتدى يتكلم عن حاجة اسمها reinforcement learning ويتكلم على transformers والتكنولوجيا اللي إحنا نعرفها النهارده.`

- token languages: {'ar': 81}
- Latin-script tokens and their `language`: `re`=ar, `in`=ar, `for`=ar, `c`=ar, `ement`=ar, `le`=ar, `ar`=ar, `ning`=ar, `trans`=ar, `form`=ar, `ers`=ar
- translation: `So why didn't I think about that? It's very clear right after he says it. So in 2016 he started talking about something called reinforcement learning and talking about transformers and the technology we know today.`
- translation keeps Latin script: True

**Segment 18** — original: `الفكرة فيها بسيطة جداً إن أنت إن إحنا لحد ما ابتدينا نعمل reinforcement learning كان طريقة برمجة الذكاء الاصطناعي عشان أبسطها يعني كنا بيبقى عندنا 3 AIs، واحد منهم اسمه The Maker اللي هو بيصنع AIs، واحد اسمه The Teacher المعلم، هو ما كانش بيعلم بس كان بيختبر الـ AIs اللي بتصنع.`

- token languages: {'ar': 129}
- Latin-script tokens and their `language`: `re`=ar, `in`=ar, `for`=ar, `c`=ar, `ement`=ar, `le`=ar, `ar`=ar, `ning`=ar, `A`=ar, `I`=ar, `s`=ar, `The`=ar, `Mak`=ar, `er`=ar, `A`=ar, `I`=ar, `s`=ar, `The`=ar, `Te`=ar, `ach`=ar, `er`=ar, `A`=ar, `I`=ar, `s`=ar
- translation: `The idea is very simple: until we started doing reinforcement learning, the way AI was programmed to simplify it—we used to have three AIs, one of them called The Maker, which makes AIs, one called The Teacher, the teacher, he wasn't teaching, but he was testing the AIs that were being built.`
- translation keeps Latin script: True

**Segment 19** — original: `وواحد هو الـ AI اللي بيصنع نفسه ده بنسميه Student أو الطالب يعني أو التلميذ. أوكي؟ وكانت الطريقة اللي إحنا بنبرمج بيها الـ AI إن إحنا بناخد مشكلة مثلاً تخيل إن المشكلة إن أنا عايز الـ AI يقرا أرقام مكتوبة باليد. كويس؟ فرقم 6 ورقم 8 بالإنجليزي شبه بعض. فكنا بنعمل بنطلب من الـ Maker المصنع الـ AIs يكتب لنا تغييرات بسيطة على الكود الأساسي اللي إحنا عملناه، ويخلق لنا مثلاً نقول آلاف من التلامذة. ناخد التلامذة دول ونوري لهم أرقام.`

- token languages: {'ar': 205}
- Latin-script tokens and their `language`: `A`=ar, `I`=ar, `Stud`=ar, `ent`=ar, `A`=ar, `I`=ar, `A`=ar, `I`=ar, `Mak`=ar, `er`=ar, `A`=ar, `I`=ar, `s`=ar
- translation: `And there was one AI that built itself, we call it a Student, meaning the student. Okay? And the way we programmed the AI was that we took a problem—say, imagine the problem is that I want the AI to read numbers handwritten. Okay? So the numbers 6 and 8 in English look similar. So we would ask the AIs manufacturer, the Maker, to write for us small changes to the basic code we wrote, and generate, for example, thousands of students. We take those students and show them numbers.`
- translation keeps Latin script: True

**Segment 20** — original: `اللي هم بوتس أو agents.`

- token languages: {'ar': 10}
- Latin-script tokens and their `language`: `ag`=ar, `ent`=ar, `s.`=ar
- translation: `Which are bots or agents.`
- translation keeps Latin script: True

**Segment 23** — original: `Small primitive AIs يعني لسه AIs مبدئية. كويس؟ بناخدهم نفرجهم على الأرقام اللي يقول إن الرقم 8 هو 8، بنخليه موجود، ونحسنه شوية عن طريق الـ Maker اللي يقول إنه لا دي 6، كنا بنمسحه. تماماً. تخيل إن أنت عندك مدرسة فيها 20,000 طالب بتوري لهم الرقم، تقول لهم ده كام، دي يقول لك 8، تقول له طب تعالى هناك هتاخد سنة تانية. اللي يقول لك لا 6 تموته. أوكي؟ ده بالضبط الطريقة اللي كتبنا بيها أوائل الـ AI Deep learning والنيورال نتوركس. جه جيفري قال لك بس يعني ليه بتعملوا كده ليه ما. ما تقول للـ AI لو قال لك دي 8 وهي 6 أو قال لك 6 وهي 8 مش فارقة، تقول له لا على فكرة ده مش 8، دي 6، تقدر أنت نفسك as a student تغير إيه في الكود بتاعك علشان تشوفها على إنها 6. فيقوم الـ AI نفسه يحسن نفسه. كويس؟ فده غير كل حاجة. غير بقى الداتا اللي إحنا نقدر نديها لهم ما بقيناش محتاجين نكلين الداتا.`

- token languages: {'ar': 360}
- Latin-script tokens and their `language`: `Sm`=ar, `all`=ar, `pri`=ar, `mit`=ar, `ive`=ar, `A`=ar, `I`=ar, `s`=ar, `A`=ar, `I`=ar, `s`=ar, `Mak`=ar, `er`=ar, `A`=ar, `I`=ar, `De`=ar, `ep`=ar, `le`=ar, `ar`=ar, `ning`=ar, `A`=ar, `I`=ar, `as`=ar, `a`=ar, `student`=ar, `A`=ar, `I`=ar
- translation: `I mean small, primitive AIs—still preliminary AIs. Okay? We take them and show them the numbers that say the number 8 is 8, we keep it there, and improve it a bit through the Maker which says: no, this is 6, we used to erase it. Exactly. Imagine you have a school with 20,000 students you show them the number, you tell them what it is, they say it's 8, you say, okay, come over there you'll take another year. And the one who says "no, 6," kills it. Okay? That's exactly the way we wrote the earliest AIs deep learning and neural networks. Then Geoffrey said, but why do you do that? Why not— Why not tell the AI, if it says it's 8 and it's 6, or says it's 6 and it's 8, it doesn't matter, you tell it, "No, actually this isn't 8," it's 6; you yourself, as a student, can change what in your code to see it, as if it's 6. So the AI itself improves itself. Okay? So that changed everything. It changed the data we could give them so we didn't have to clean the data.`
- translation keeps Latin script: True

Per-token `language` distribution across all original final tokens:

```
{'ar': 1768}
```

## Q4. `source_language` on translation tokens

| source_language | language | tokens |
| --- | --- | --- |
| `ar` | `en` | 1481 |

## Q5. Delay from a word's `end_ms` to its translation arriving

Measured against the real-time send clock (`recv_offset_ms - end_ms`), so this is end-to-end wall clock.

- original final token after the word was spoken: median **1717 ms**, p90 3731 ms, min 554, max 6410 (n=1768)
- first translation token after the segment's last word: median **576 ms**, p90 701 ms, min -55476, max 870 (n=28)
- last translation token after the segment's last word: median **649 ms**, p90 839 ms, min 430, max 923 (n=28)

## Sample segments

- **0** `فعلاً معمولة من البروتينات، بالنسبة لأي حد عايز يفهم علم البيولوجي، عايز يقدر يتخيل شكل البروتين ده إيه. لكن البروتين فولدينج دي كانت مشكلة بتاخد واحد مثلاً بيدرس بيولوجي على درجة الدكتوراه، بتاخد تيم مع بعضه ياخد مثلاً 4 سنين عشان to fold one protein. فلما ظهر ألفا فولد، ألفا فولد وقتها كان يمكن مثلاً فيه 40,000 بروتين مفهوم للعالم، ألفا فولد استنتج الشكل 200 مليون بروتين.`
  - → `It's actually made from proteins, for anyone who wants to understand biology, and wants to be able to imagine what this protein looks like. But folding protein was a problem that would take someone, for example, a biology student in a PhD degree, and it would take a team together to spend, say, 4 years to fold one protein. So when AlphaFold appeared—AlphaFold at the time was maybe, for example, there were 40,000 proteins understood by the world—AlphaFold inferred the shape of 200 million proteins.`
- **1** `يا نهار أبيض!`
  - → `Oh my God!`
- **2** `كويس؟ كل بروتين معروف على.`
  - → `Okay? Every protein is known to...`
- **3** `الأرض، وكمان ابتدى يتخيل بروتينات جديدة ممكن نعمل منها أجهزة جديدة بيولوجية. تمام؟ فبذكاء اصطناعي في مجرد إن أنت دربت للذكاء الاصطناعي على وقتها البروتينز اللي موجودة، قلت له بقية البروتينز هتتشكل إزاي، راح مشكلها كلها. فده كان في رأيي أنا تغيير واضح وصريح للعالم. ليه؟ لأن أنا النهارده لما يجي لي فيروس، الفيروس بيتعامل مع جسمي إزاي؟ إن هو بيتعامل مع البروتينات دي. فأنا لو مش فاهم شكل البروتين، وعندي شكل ثري دايمنشنال ليه، أقدر أشوف أنا هركب إيه على البروتين ده عشان يعالج ده وهكذا. من 2016 برضه كان جيفري هينتونز زميل عزيز في جوجل، جيفري أعتقد في رأيي أنا هو اللي يعني بيسموه جد الإي آي.`
  - → `On Earth, and it also started to imagine new proteins we could make from, new biological devices. Right? So with AI, the moment you train AI on the proteins that exist at the time, you tell it how the rest of the proteins will form, and it forms them all. So in my opinion, that was a clear, straightforward change for the world. Why? Because today, when a virus hits me, the virus interacts with my body how? It interacts with these proteins. So if I don't understand the shape of a protein, and I have a three-dimensional shape of it, I can see what I'm going to put on that protein to treat it, and so on. Since 2016 as well, Geoffrey Hinton, a dear colleague, at Google—Geoffrey, I think in my opinion, is the one they call the grandfather of AI.`
- **4** `كنت هقول أبو الإي آي آه.`
  - → `I was going to say the father of AI, yeah.`
- **5** `أو أبو الإي آي.`
  - → `Or the father of AI.`
- **6** `في رأيي أنا إن ده حقيقي، إنه جيفري.`
  - → `In my opinion, that's true—that it's Geoffrey.`
- **7** `بس الأودينس بتوعنا بس.`
  - → `But only our audience.`
- **8** `ده زي ما أنت بتقول يعتبر أبو. فده زميل جودت بس عشان الناس تبقى عارفة.`
  - → `That, like you're saying, is considered the father. So it's just a colleague of Goudat, so people know.`
- **9** `لا، هو جيفري. جيفري الحقيقة يعني في رأيي أنا معجزة. ليه؟ لأن هو أولاً اشتغل في الإي آي في سنين كتير جداً، بس هو كان دايماً بيوضح لنا حاجات بسيطة جداً. اللي هو بعد عارف، عارف لما تبقى قاعد مع حد وبعدين يقول لك.`
  - → `No, it's Geoffrey. Geoffrey, honestly, in my opinion, is a miracle. Why? Because first, he worked in AI for many years, but he always explained very simple things to us. Like, you know—when you're sitting with someone and then he tells you.`

