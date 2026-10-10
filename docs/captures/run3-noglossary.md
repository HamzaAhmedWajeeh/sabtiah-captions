# Soniox probe analysis: `run3-noglossary.jsonl`

- audio: `recordings\podcast-5min.m4a`
- responses: 1672
- endpoint-bounded segments: 31

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
| 9 | 87 | 0 | 0 |
| 10 | 2 | 0 | 0 |
| 11 | 25 | 0 | 0 |
| 12 | 10 | 0 | 0 |
| 13 | 70 | 0 | 0 |
| 14 | 11 | 0 | 0 |
| 15 | 15 | 0 | 0 |
| 16 | 2 | 0 | 0 |
| 17 | 7 | 0 | 0 |
| 18 | 256 | 0 | 0 |
| 19 | 7 | 0 | 0 |
| 20 | 4 | 0 | 0 |
| 21 | 2 | 0 | 0 |
| 22 | 2 | 0 | 0 |
| 23 | 2 | 0 | 0 |
| 25 | 16 | 0 | 0 |
| 26 | 339 | 0 | 0 |
| 27 | 8 | 0 | 0 |
| 28 | 124 | 0 | 0 |
| 29 | 21 | 0 | 0 |
| 30 | 8 | 0 | 0 |

Segments whose translation finished only after `<end>`: **0 of 30**.

## Q2. Is there an end marker on the translation side?

`<end>` tokens carrying `translation_status: "translation"`: **0**.

## Q3. Token languages in mixed sentences

Segments whose original text mixes Arabic and Latin script: **7 of 31**.

**Segment 0** — original: `فعلاً معمولة من البروتينات، بالنسبة لأي حد عايز يفهم علم البيولوجي، عايز يقدر يتخيل شكل البروتين ده إيه. لكن البروتين فولدينج دي كانت مشكلة بتاخد واحد مثلاً بيدرس بيولوجي على درجة الدكتوراه، بتاخد تيم مع بعضه ياخد مثلاً 4 سنين عشان to fold one protein. فلما ظهر ألفا فولد، ألفا فولد وقتها كان يمكن مثلاً فيه 40,000 بروتين مفهوم للعالم، ألفا فولد استنتج الشكل 200 مليون بروتين.`

- token languages: {'ar': 180}
- Latin-script tokens and their `language`: `to`=ar, `f`=ar, `old`=ar, `one`=ar, `prote`=ar, `in.`=ar
- translation: `It's actually made from proteins, for anyone who wants to understand biology, and wants to be able to imagine what this protein looks like. But folding protein was a problem that would take someone, for example, a biology student in a PhD degree, and it would take a team together to spend, say, 4 years to fold one protein. So when AlphaFold appeared—AlphaFold at the time was maybe, for example, there were 40,000 proteins understood by the world—AlphaFold inferred the shape of 200 million proteins.`
- translation keeps Latin script: True

**Segment 13** — original: `طب أنا ليه ما فكرتش فيها دي؟ يعني واضحة جداً بعد ما يقولها. فهو 2016 ابتدى يتكلم عن حاجة اسمها reinforcement learning، ويتكلم على transformers والتكنولوجيا اللي إحنا نعرفها النهاردة.`

- token languages: {'ar': 82}
- Latin-script tokens and their `language`: `re`=ar, `in`=ar, `for`=ar, `c`=ar, `ement`=ar, `le`=ar, `ar`=ar, `ning`=ar, `trans`=ar, `form`=ar, `ers`=ar
- translation: `So why didn't I think about that? It's very clear right after he says it. So in 2016 he started talking about something called reinforcement learning, and he talked about Transformers and the technology we know today.`
- translation keeps Latin script: True

**Segment 18** — original: `الفكرة فيها بسيطة جداً إن أنت إن إحنا لحد ما ابتدينا نعمل reinforcement learning كان طريقة برمجة الذكاء الاصطناعي عشان أبسطها يعني كنا بيبقى عندنا 3 AIs، واحد منهم اسمه The Maker، اللي هو بيصنع AIs، وواحد اسمه The Teacher، المعلم، هو ما كانش بيعلم بس كان بيختبر الـ AIs اللي بتصنع. وواحد هو الـ AI اللي بيصنع نفسه ده بنسميه Student أو الطالب يعني أو التلميذ. أوكي؟ وكانت الطريقة اللي إحنا بنبرمج بيها الـ AI إن إحنا بناخد مشكلة مثلاً، تخيل إن المشكلة إن أنا عايز الـ AI يقرا أرقام مكتوبة باليد. كويس؟ فرقم 6 ورقم 8 بالإنجليزي شبه بعض. فكنا بنعمل بنطلب من الـ Maker، المصنع الـ AIs، يكتب لنا تغييرات بسيطة على الكود الأساسي اللي إحنا عملناه، ويخلق لنا مثلاً، نقول آلاف من التلامذة. ناخد التلامذة دول ونوري لهم أرقام.`

- token languages: {'ar': 342}
- Latin-script tokens and their `language`: `re`=ar, `in`=ar, `for`=ar, `c`=ar, `ement`=ar, `le`=ar, `ar`=ar, `ning`=ar, `A`=ar, `I`=ar, `s`=ar, `The`=ar, `Mak`=ar, `er`=ar, `A`=ar, `I`=ar, `s`=ar, `The`=ar, `Te`=ar, `ach`=ar, `er`=ar, `A`=ar, `I`=ar, `s`=ar, `A`=ar, `I`=ar, `Stud`=ar, `ent`=ar, `A`=ar, `I`=ar, `A`=ar, `I`=ar, `Mak`=ar, `er`=ar, `A`=ar, `I`=ar, `s`=ar
- translation: `The idea is very simple: that we, until we started doing reinforcement learning, the way AI was programmed—to simplify it— we used to have three AIs: one of them was called The Maker, which makes AIs, and one was called The Teacher, the teacher, he didn't teach, but he tested the AIs that were being made. And there was one AI that made itself, we call it a Student, meaning the student, or the apprentice. Okay? And the way we programmed the AI was that we would take a problem, for example, imagine the problem is that I want the AI to read numbers written by hand. Okay? So the numbers 6 and 8 in English look similar. So we would ask the Maker, the AIs manufacturer, to write for us simple changes to the basic code we wrote, and generate, for example, let's say thousands of students. We take those students and show them numbers.`
- translation keeps Latin script: True

**Segment 25** — original: `Small primitive AIs، يعني لسه AIs مبدئية.`

- token languages: {'ar': 21}
- Latin-script tokens and their `language`: `Sm`=ar, `all`=ar, `pri`=ar, `mit`=ar, `ive`=ar, `A`=ar, `I`=ar, `s`=ar, `A`=ar, `I`=ar, `s`=ar
- translation: `Meaning small primitive AIs, still basic AIs.`
- translation keeps Latin script: True

**Segment 26** — original: `كويس؟ بناخدهم نفرجهم على الأرقام اللي يقول إن الرقم 8 هو 8، بنخليه موجود، ونحسنه شوية عن طريق الـ Maker، اللي يقول إنه لا دي 6، كنا بنمسحه. تماماً. تخيل إن أنت عندك مدرسة فيها 20,000 طالب بتوري لهم الرقم، تقول لهم ده كام؟ دي يقول لك 8، تقول له طب تعال هناك هتاخد سنة تانية. اللي يقول لك لا 6 تموته. أوكي؟ ده بالضبط الطريقة اللي كتبنا بيها أوائل الـ AI deep learning والنيورال نتوركس. جه جيفري قال لك بس يعني ليه بتعملوا كده؟ ليه ما. ما تقول للـ AI لو قال لك دي 8 وهي 6، أو قال لك 6 وهي 8، مش فارقة، تقول له لا على فكرة ده مش 8، دي 6، تقدر أنت نفسك as a student تغير إيه في الكود بتاعك علشان تشوفها على إنها 6. فيقوم الـ AI نفسه يحسن نفسه. كويس؟ فده غير كل حاجة. غير بقى الداتا اللي إحنا نقدر نديها لهم ما بقيناش محتاجين نكلين الداتا.`

- token languages: {'ar': 343}
- Latin-script tokens and their `language`: `Mak`=ar, `er`=ar, `A`=ar, `I`=ar, `de`=ar, `ep`=ar, `le`=ar, `ar`=ar, `ning`=ar, `A`=ar, `I`=ar, `as`=ar, `a`=ar, `student`=ar, `A`=ar, `I`=ar
- translation: `Okay? We take them and show them the numbers that say the number 8 is 8, we keep it there, and improve it a bit through the Maker, that says no, this is 6, we used to erase it. Exactly. Imagine you have a school with 20,000 students and you show them the number, you ask them, "What is that?" They tell you, "8." You say, "Okay, come over there you'll take another year." And the one who says "no, 6, kill it." Okay? That's exactly the way we wrote the early days of AI deep learning and neural networks. Then Geoffrey came and said, but I mean, why do you do that? Why not... Why don't you tell the AI, if it tells you this is 8 and it's 6, or it says 6 and it's 8, it doesn't matter, you tell it, "No, actually this isn't 8," this is 6, you yourself, as a student, can change what in your code so you can see it as 6. So the AI itself improves itself. Okay? So that changed everything. It changed the data we could give them we no longer needed to clean the data.`
- translation keeps Latin script: True

**Segment 28** — original: `ومن 2016 ابتدت الحياة بقى تتقدم بسرعة عالية جداً، وظهر في 2023 واحد من الأمثلة كان تشات جي بي تي، بس تشات جي بي تي زي ما تقول كده كسر الاتفاقية اللي ما بينا. ليه؟ لأن إحنا كلنا كنا بنقول إن إحنا بنخلق معجزات بالـ AI، بس إحنا مش عارفين إذا كان الـ AI ده يعني على درجة من الأمان للبشرية تسمح إن إحنا نحطه في على شبكة الإنترنت.`

- token languages: {'ar': 154}
- Latin-script tokens and their `language`: `A`=ar, `I`=ar, `A`=ar, `I`=ar
- translation: `And from 2016, life started advancing at a very high pace, and in 2023 one of the examples appeared it was ChatGPT, but ChatGPT, as you could say, broke the agreement between us. Why? Because we were all saying that we create miracles with AI, but we didn't know whether this AI was at a level of safety for humanity that would allow us to put it on the internet.`
- translation keeps Latin script: True

Per-token `language` distribution across all original final tokens:

```
{'ar': 1783}
```

## Q4. `source_language` on translation tokens

| source_language | language | tokens |
| --- | --- | --- |
| `ar` | `en` | 1508 |

## Q5. Delay from a word's `end_ms` to its translation arriving

Measured against the real-time send clock (`recv_offset_ms - end_ms`), so this is end-to-end wall clock.

- original final token after the word was spoken: median **1505 ms**, p90 3257 ms, min 535, max 6219 (n=1783)
- first translation token after the segment's last word: median **614 ms**, p90 769 ms, min -56743, max 1630 (n=30)
- last translation token after the segment's last word: median **658 ms**, p90 794 ms, min 566, max 1630 (n=30)

## Sample segments

- **0** `فعلاً معمولة من البروتينات، بالنسبة لأي حد عايز يفهم علم البيولوجي، عايز يقدر يتخيل شكل البروتين ده إيه. لكن البروتين فولدينج دي كانت مشكلة بتاخد واحد مثلاً بيدرس بيولوجي على درجة الدكتوراه، بتاخد تيم مع بعضه ياخد مثلاً 4 سنين عشان to fold one protein. فلما ظهر ألفا فولد، ألفا فولد وقتها كان يمكن مثلاً فيه 40,000 بروتين مفهوم للعالم، ألفا فولد استنتج الشكل 200 مليون بروتين.`
  - → `It's actually made from proteins, for anyone who wants to understand biology, and wants to be able to imagine what this protein looks like. But folding protein was a problem that would take someone, for example, a biology student in a PhD degree, and it would take a team together to spend, say, 4 years to fold one protein. So when AlphaFold appeared—AlphaFold at the time was maybe, for example, there were 40,000 proteins understood by the world—AlphaFold inferred the shape of 200 million proteins.`
- **1** `يا نهار أبيض!`
  - → `Oh my God!`
- **2** `كويس؟ كل بروتين معروف على.`
  - → `Okay? Every protein is known to...`
- **3** `الأرض، وكمان ابتدى يتخيل بروتينات جديدة ممكن نعمل منها أجهزة جديدة بيولوجية. تمام؟ فبذكاء اصطناعي في مجرد إن أنت دربت الذكاء الاصطناعي على وقتها البروتينز اللي موجودة، قلت له بقية البروتينز هتتشكل إزاي، راح مشكلها كلها. فده كان في رأيي أنا تغيير واضح وصريح للعالم. ليه؟ لأن أنا النهارده لما يجي لي فيروس، الفيروس بيتعامل مع جسمي إزاي؟ إن هو بيتعامل مع البروتينات دي، فأنا لو مش فاهم شكل البروتين وعندي شكل ثري دايمنشنال ليه أقدر أشوف أنا هركب إيه على البروتين ده عشان يعالج ده وهكذا. من 2016 برضه كان جيفري هينتونز زميل عزيز في جوجل، جيفري أعتقد في رأيي أنا هو اللي يعني بيسموه جد الإي آي.`
  - → `On Earth, and it also started to imagine new proteins that we could use to make new biological devices. Right? So with artificial intelligence, just by training the proteins that exist at the time, you tell it how the rest of the proteins will form, and it forms them all. So in my opinion, that was a clear and straightforward change for the world. Why? Because today, when a virus hits me, how does the virus interact with my body? It interacts with these proteins, so if I don't understand the protein's shape and have a three-dimensional view, how can I see what I'm going to put on that protein to treat it, and so on. Since 2016 as well, Geoffrey Hinton, a dear colleague, at Google—Geoffrey, I think in my opinion, is the one they call the grandfather of AI.`
- **4** `كنت هقول أبو الإي آي آه.`
  - → `I was going to say the father of AI, yeah.`
- **5** `أو أبو الإي آي.`
  - → `Or the father of AI.`
- **6** `في رأيي أنا إن ده حقيقي، إنه جيفري.`
  - → `In my opinion, that's true—that it's Geoffrey.`
- **7** `بس الأودينس بتوعنا بس.`
  - → `But only our audience.`
- **8** `ده زي ما أنت بتقول يعتبر أبو. فده زميل جودت بس عشان الناس تبقى عارفة.`
  - → `That, like you're saying, is considered the father. So this is just a colleague of Goudat so people know.`
- **9** `لا، هو جيفري. جيفري الحقيقة يعني في رأيي أنا معجزة، ليه؟ لأن هو أولاً اشتغل في الإي آي في سنين كتير جداً، بس هو كان دايماً بيوضح لنا حاجات بسيطة جداً. اللي هو بعد عارف، عارف لما تبقى قاعد مع حد وبعدين يقول لك.`
  - → `No, it's Geoffrey. Geoffrey, honestly, in my opinion, is a miracle, why? Because first, he worked in AI for many very many years, but he was always showing us very simple things. Like, you know—when you're sitting with someone and then he tells you.`

