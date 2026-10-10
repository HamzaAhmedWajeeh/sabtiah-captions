# Soniox probe analysis: `run5-english.jsonl`

- audio: `recordings\english-2min.m4a`
- responses: 682
- endpoint-bounded segments: 33

## Q1. Translation tokens arriving after the segment's `<end>`

| segment | translation tokens | arrived after `<end>` | last one (ms after `<end>`) |
| --- | --- | --- | --- |
| 0 | 324 | 0 | 0 |
| 1 | 39 | 0 | 0 |
| 2 | 3 | 0 | 0 |
| 3 | 3 | 0 | 0 |
| 4 | 26 | 0 | 0 |
| 5 | 5 | 0 | 0 |
| 6 | 49 | 0 | 0 |
| 7 | 13 | 0 | 0 |
| 8 | 15 | 0 | 0 |
| 9 | 4 | 0 | 0 |
| 10 | 3 | 0 | 0 |
| 11 | 19 | 0 | 0 |
| 12 | 4 | 0 | 0 |
| 13 | 5 | 0 | 0 |
| 14 | 111 | 0 | 0 |
| 15 | 2 | 0 | 0 |
| 16 | 46 | 0 | 0 |
| 17 | 9 | 0 | 0 |
| 18 | 3 | 0 | 0 |
| 19 | 13 | 0 | 0 |
| 20 | 8 | 0 | 0 |
| 21 | 30 | 0 | 0 |
| 22 | 3 | 0 | 0 |
| 23 | 31 | 0 | 0 |
| 24 | 4 | 0 | 0 |
| 25 | 4 | 0 | 0 |
| 26 | 7 | 0 | 0 |
| 27 | 11 | 0 | 0 |
| 28 | 3 | 0 | 0 |
| 29 | 14 | 0 | 0 |
| 30 | 9 | 0 | 0 |
| 31 | 12 | 0 | 0 |
| 32 | 11 | 0 | 0 |

Segments whose translation finished only after `<end>`: **0 of 33**.

## Q2. Is there an end marker on the translation side?

`<end>` tokens carrying `translation_status: "translation"`: **0**.

## Q3. Token languages in mixed sentences

Segments whose original text mixes Arabic and Latin script: **0 of 33**.

_No mixed-script segment in this capture._

Per-token `language` distribution across all original final tokens:

```
{'en': 680}
```

## Q4. `source_language` on translation tokens

| source_language | language | tokens |
| --- | --- | --- |
| `en` | `ar` | 843 |

## Q5. Delay from a word's `end_ms` to its translation arriving

Measured against the real-time send clock (`recv_offset_ms - end_ms`), so this is end-to-end wall clock.

- original final token after the word was spoken: median **1306 ms**, p90 2558 ms, min 618, max 5285 (n=680)
- first translation token after the segment's last word: median **695 ms**, p90 815 ms, min -49140, max 931 (n=33)
- last translation token after the segment's last word: median **716 ms**, p90 866 ms, min 659, max 931 (n=33)

## Sample segments

- **0** `All over the world, in North America, South America, Europe, Asia, Africa, Argentina, Belgium, Mongolia, Tanzania, West Germany, and many other places, apparently had large deposits of dinosaur fossils never before seen. That's because we find the first one, and then we're like, "Holy shit, there must be more of these out there." And then the bone wars started between Ogden and Marsh. You had the Mantell collection with the first Iguanodon that was found, and they put it, and they made these really stupid, uh, statues, and put them in crystal palace in London. And, uh, Mary Anning—one of the first fem—well, the first female paleontologist and one of the first people to actually find fossils—she discovered the ichthyosaur. Mary Anning was the girl from the Rhine; she sells seashells on the seashore, because that's what she did as a child.`
  - → `في جميع أنحاء العالم، في أمريكا الشمالية وأمريكا الجنوبية، وأوروبا وآسيا وأفريقيا وأرجنتين وبلجيكا ومنغوليا، وتنزانيا وألمانيا الغربية والعديد من الأماكن الأخرى، يُقال إنها كانت تضم ردائعًا كبيرة لحفريات ديناصورات لم تُرَ من قبل. وذلك لأننا نعثر على الأولى، ثم نقول: «يا إلهي، لا بد أن هناك المزيد من هذه هناك». ثم بدأت حروب العظام بين أوغدن ومارش. كانت هناك مجموعة مانتيل بأول إيغوانودون تم العثور عليه، ووضعوه، وصنعوا هذه التماثيل الغبية جدًا، آه، ووضعوها في قصر الكريستال في لندن. وآه، ماري آنينغ—واحدة من أوائل المرأ— أعني، أول عالمة أحافير أنثى وواحدة من أوائل من عثروا فعليًا على أحفورة— اكتشفت الإيكثيوسور. كانت ماري آنينغ الفتاة من نهر الراين؛ وهي تبيع أصدافًا على ساحل البحر، لأن هذا ما كانت تفعله عندما كانت طفلة.`
- **1** `And as she was looking for seashells, she found a skeleton of an ichthyosaur buried in a cliff.`
  - → `وأثناء بحثها عن أصداف، عثرت على هيكل عظمي لإيكثيوسور مدفون في منحدر صخري.`
- **2** `Whoa.`
  - → `واو.`
- **3** `Yeah.`
  - → `نعم.`
- **4** `Like a kid. I think she was 12, 13.`
  - → `مثل طفل. أظن أنها كانت في الثانية عشرة أو الثالثة عشرة.`
- **5** `Holy shit.`
  - → `يا إلهي.`
- **6** `I think paleontology Twitter is going to rip me apart for not knowing the thing. But come on, I'm hyped up now.`
  - → `أعتقد أن تويتر الخاص بالعلم الأحفوري سيمزقني إربًا لأنني لا أعرف هذا الشيء. لكن هيا، أنا متحمس جدًا الآن.`
- **7** `These trunkies, hyped up.`
  - → `هؤلاء الأوغاد، متحمسون.`
- **8** `I'm not—dude, that's one beer.`
  - → `أنا لست—يا رجل، هذه بيرة واحدة.`
- **9** `All right.`
  - → `حسنًا.`

