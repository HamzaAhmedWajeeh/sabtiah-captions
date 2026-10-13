"""Phase 2: `app/captions.py` driven entirely by replayed captures.

No Soniox calls. Every test here runs off `tests/fixtures/*.jsonl` or a small
synthetic response, which is what CLAUDE.md's budget rule requires and what
makes this layer cheap to iterate on.

The four fixtures are not interchangeable; each pins down behaviour the others
cannot (see docs/soniox-notes.md):

- `run1`          two-way `ar`->`en`, 30 endpoints: the ordinary case
- `run2-oneway`   the one-way config used as the Phase 6 fallback
- `run4-saudi`    **one endpoint in 300 s** - the no-paragraph-break case
- `run5-english`  the only `en`->`ar` data, English originals tagged `en`
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.captions import (  # noqa: E402
    CLAUSE_ENDERS,
    CLOSERS,
    SENTENCE_ENDERS,
    CaptionState,
    Paragraph,
)
from app.replay import FakeSonioxClient, load_capture, responses  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"
ALL_FIXTURES = ["run1", "run2-oneway", "run4-saudi", "run5-english"]

ARABIC = re.compile(r"[؀-ۿ]")
LATIN_RUN = re.compile(r"[A-Za-z][A-Za-z0-9.'\-]*")


def fixture(name: str) -> Path:
    return FIXTURES / f"{name}.jsonl"


def replay(name: str, **kwargs) -> CaptionState:
    state = CaptionState(**kwargs)
    for response in responses(fixture(name)):
        state.add_response(response)
    return state


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def latin_words(text: str) -> set[str]:
    return {
        m.group(0).strip(".'-")
        for m in LATIN_RUN.finditer(text)
        if len(m.group(0)) > 1
    }


def final_token_text(name: str, language: str) -> str:
    """Every final token Soniox routed to `language`, concatenated verbatim."""
    pieces = []
    for response in responses(fixture(name)):
        for token in response.get("tokens") or []:
            text = token.get("text", "")
            if not text or text == "<end>" or not token.get("is_final"):
                continue
            if token.get("language") == language:
                pieces.append(text)
    return "".join(pieces)


def token(text, *, final=True, lang="ar", status="original", **extra):
    tok = {"text": text, "is_final": final, "translation_status": status}
    if lang is not None:
        tok["language"] = lang
    tok.update(extra)
    return tok


def response(*tokens):
    return {"tokens": list(tokens), "final_audio_proc_ms": 0}


# ---------------------------------------------------------------------------
# The spec's five tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ALL_FIXTURES)
@pytest.mark.parametrize("language", ["ar", "en"])
def test_text_reconstructs_exactly(name, language):
    """No duplication, no loss, and no word split at a paragraph break.

    This is the strongest statement available about the whole assembly: the
    paragraphs, rejoined with a single space, must equal the verbatim
    concatenation of the final tokens Soniox routed to this language.

    It catches both spec'd failure modes at once. Appending a re-sent non-final
    buffer instead of replacing it duplicates text, which lengthens the result.
    Breaking a paragraph anywhere other than a word boundary inserts the join
    space *inside* a word - which is exactly how "Meaning" became "Me aning"
    while this was being written.
    """
    state = replay(name)
    want = normalise(final_token_text(name, language))
    got = normalise(" ".join(p.final for p in state.streams[language]))
    assert got == want


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_end_token_never_appears_in_output(name):
    state = replay(name)
    for language in ("ar", "en"):
        rendered = state.render(language)
        assert "<end>" not in rendered
        assert "end>" not in rendered
        for para in state.streams[language]:
            assert "<end>" not in para.final
            assert "<end>" not in para.partial


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_english_view_contains_no_arabic_script(name):
    """BUILD_SPEC section 6, and it holds on every capture."""
    state = replay(name)
    rendered = state.render("en")
    found = ARABIC.findall(rendered)
    assert not found, f"Arabic script leaked into the en stream: {found[:20]}"


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_arabic_view_latin_is_transcribed_speech(name):
    """The honest form of the spec's "no Latin except glossary terms" test.

    That test cannot pass as written, and the data says the spec's expectation
    was wrong rather than the code: `run1`'s Arabic stream legitimately
    contains `AI`, `transformers`, `bias`, `hallucinations`, `The Maker` and a
    dozen more. The Egyptian speakers said those words in English, Soniox
    transcribed them in Latin script, and they belong in the Arabic captions -
    only one of them (`agents`) is in config/glossary.yaml.

    What the spec was actually protecting against is English *leaking* into the
    Arabic stream through mis-routing. So the invariant is provenance: every
    Latin word in the `ar` stream must be present in the Arabic-side source
    text. Nothing may be invented by the routing.
    """
    state = replay(name)
    source = final_token_text(name, "ar")
    leaked = latin_words(state.render("ar")) - latin_words(source)
    assert not leaked, f"Latin in ar stream with no Arabic-side source: {leaked}"


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_paragraph_count_is_stable_across_replays(name):
    first = replay(name)
    second = replay(name)
    for language in ("ar", "en"):
        assert len(first.streams[language]) == len(second.streams[language])
        assert first.render(language) == second.render(language)
    assert first.seq == second.seq


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_paragraph_cap_holds(name):
    state = replay(name, max_paragraphs=5, max_paragraph_chars=80)
    for language in ("ar", "en"):
        assert len(state.streams[language]) <= 5
    # the cap must drop the oldest, so the newest text has to survive
    unconstrained = replay(name, max_paragraph_chars=80)
    for language in ("ar", "en"):
        if unconstrained.streams[language]:
            assert (
                state.streams[language][-1].final
                == unconstrained.streams[language][-1].final
            )


# ---------------------------------------------------------------------------
# Behaviour the Phase 1 captures forced into the design
# ---------------------------------------------------------------------------


def test_soft_break_engages_when_endpoints_never_arrive():
    """`run4-saudi` is one 300 s segment with a single `<end>`.

    Without a size-based break this is a single ever-growing paragraph per
    stream, which is unrenderable and defeats the 200-paragraph cap. The spec's
    rule alone is not enough; this is the regression test for that deviation.
    """
    endpoints = sum(
        1
        for r in responses(fixture("run4-saudi"))
        for t in (r.get("tokens") or [])
        if t.get("text") == "<end>" and t.get("translation_status") != "translation"
    )
    assert endpoints == 1, "fixture changed; this test's premise is gone"

    state = replay("run4-saudi")
    for language in ("ar", "en"):
        paragraphs = state.streams[language]
        assert len(paragraphs) > 5, (
            f"{language} collapsed into {len(paragraphs)} paragraph(s); "
            "the soft break is not working"
        )
        for para in paragraphs[:-1]:
            assert len(para.final) < 2 * state.max_paragraph_chars


def test_end_token_with_status_none_breaks_a_paragraph():
    """`<end>` carries `translation_status: "none"`, not `"original"`.

    Selecting original tokens with `== "original"` drops every endpoint and the
    stream never breaks. This pins the "not translation" test.
    """
    state = CaptionState()
    state.add_response(response(token("مرحبا", lang="ar")))
    state.add_response(
        response({"text": "<end>", "is_final": True, "translation_status": "none",
                  "confidence": 1})
    )
    state.add_response(response(token(" ثانية", lang="ar")))
    assert len(state.streams["ar"]) == 2
    assert state.streams["ar"][0].closed


def test_english_to_arabic_routes_by_output_language():
    """`run5-english`: English speech must land in `en`, its translation in `ar`."""
    state = replay("run5-english")
    assert state.streams["en"], "no English paragraphs from English speech"
    assert state.streams["ar"], "no Arabic paragraphs from en->ar translation"
    # English original is Latin; the Arabic translation is Arabic script
    assert not ARABIC.search(state.render("en"))
    assert ARABIC.search(state.render("ar"))


def test_translated_paragraphs_carry_no_partial():
    """Translation tokens are always final (4,493 of 4,493 across captures).

    So in `run1` the `en` stream is pure translation and must end with no
    partial text pending, while the Arabic original side does use partials.
    """
    state = replay("run1")
    assert all(p.partial == "" for p in state.streams["en"])


def test_non_final_buffer_is_replaced_not_appended():
    """The buffer *grows* between responses, which is what exposes appending.

    Re-sending an identical buffer does not: a naive `partial += text` is
    hidden by any "only write when it differs" guard, because after the first
    append the buffer already equals the incoming text. Soniox re-sends the
    whole unfinalised tail every response, so the realistic sequence is a
    lengthening prefix - and appending that produces visible garbage.
    """
    state = CaptionState()
    for text in ("hel", "hello", "hello wor", "hello world"):
        state.add_response(response(token(text, final=False, lang="en")))
    para = state.streams["en"][-1]
    assert para.partial == "hello world"
    assert para.final == ""

    # a final for the same words then clears the partial
    state.add_response(response(token("hello world.", lang="en")))
    para = state.streams["en"][-1]
    assert para.final == "hello world."
    assert para.partial == ""


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_partials_match_the_last_response_exactly(name):
    """On real data, the pending partial is exactly the last non-final buffer.

    Anything accumulated across responses shows up here immediately.
    """
    records = list(responses(fixture(name)))
    state = CaptionState()
    for resp in records:
        state.add_response(resp)

    expected: dict[str, str] = {"ar": "", "en": ""}
    for resp in reversed(records):
        if resp.get("tokens"):
            for tok in resp["tokens"]:
                if tok.get("is_final") or tok.get("text") == "<end>":
                    continue
                lang = tok.get("language")
                if lang in expected:
                    expected[lang] += tok.get("text", "")
            break

    for language in ("ar", "en"):
        pending = "".join(p.partial for p in state.streams[language])
        assert pending == expected[language].lstrip() or pending == expected[language]


def test_partial_clears_when_a_response_sends_no_non_finals():
    state = CaptionState()
    state.add_response(response(token("partial", final=False, lang="en")))
    assert state.streams["en"][-1].partial == "partial"
    state.add_response(response(token("final.", lang="en")))
    assert state.streams["en"][-1].partial == ""


def test_error_response_is_a_noop():
    """Error responses carry `tokens: []` alongside `error_type` (Phase 1)."""
    state = replay("run1")
    before = (state.seq, state.snapshot())
    changed = state.add_response(
        {
            "tokens": [],
            "error_code": 402,
            "error_type": "organization_balance_exhausted",
            "request_id": "x",
        }
    )
    assert changed is False
    assert (state.seq, state.snapshot()) == before


def test_translation_without_target_language_infers_from_source():
    state = CaptionState()
    state.add_response(
        response(
            {"text": "hello", "is_final": True, "translation_status": "translation",
             "source_language": "ar"}
        )
    )
    assert state.streams["en"][-1].final == "hello"


# ---------------------------------------------------------------------------
# The broadcast contract Phase 3 depends on
# ---------------------------------------------------------------------------


def test_snapshot_shape():
    state = replay("run1")
    snap = state.snapshot()
    assert snap["type"] == "snapshot"
    assert snap["seq"] == state.seq
    assert snap["live"] is False
    assert set(snap["streams"]) == {"ar", "en"}
    para = snap["streams"]["ar"][0]
    assert set(para) == {"id", "final", "partial"}


def test_diff_contains_only_changed_paragraphs():
    state = replay("run1")
    mark = state.seq
    assert state.diff_since(mark)["streams"] == {"ar": [], "en": []}

    state.add_response(response(token(" إضافة", lang="ar")))
    diff = state.diff_since(mark)
    assert diff["seq"] > mark
    assert len(diff["streams"]["ar"]) == 1
    assert diff["streams"]["en"] == []


def test_diff_from_zero_matches_the_snapshot():
    state = replay("run5-english")
    assert state.diff_since(0)["streams"] == state.snapshot()["streams"]


def test_paragraph_ids_are_unique_and_stable():
    state = replay("run1")
    ids = [p.id for paras in state.streams.values() for p in paras]
    assert len(ids) == len(set(ids))
    # an id must keep pointing at the same paragraph as text is appended
    first = state.streams["ar"][0]
    before = first.id
    state.add_response(response(token(" ذيل", lang="ar")))
    assert state.streams["ar"][0].id == before


def test_set_live_bumps_seq_only_on_change():
    state = CaptionState()
    assert state.live is False
    state.set_live(True)
    bumped = state.seq
    assert bumped > 0
    state.set_live(True)
    assert state.seq == bumped


# ---------------------------------------------------------------------------
# The replay seam itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_replay_reads_every_response(name):
    meta, records = load_capture(fixture(name))
    assert meta["config"]["model"] == "stt-rt-v5"
    assert len(records) == len(list(responses(fixture(name))))
    assert all("recv_offset_ms" in r for r in records)


async def test_fake_client_yields_responses_without_network():
    client = FakeSonioxClient(fixture("run5-english"))
    state = CaptionState()
    count = 0
    async for resp in client.__aiter__():
        state.add_response(resp)
        count += 1
    assert count == len(client)
    assert state.render("ar")
    assert 119 <= client.audio_seconds <= 121


def test_fixtures_carry_no_credentials():
    """Security invariant: a capture must never contain the API key."""
    for name in ALL_FIXTURES:
        text = fixture(name).read_text(encoding="utf-8")
        assert "SONIOX" not in text
        assert "api_key" not in text
        assert "Bearer" not in text


def test_no_audio_in_fixtures():
    """The recordings are third-party YouTube audio and must never be committed."""
    for pattern in ("*.m4a", "*.wav", "*.mp3", "*.aac", "*.opus"):
        assert not list(FIXTURES.glob(pattern))


def test_captions_module_stays_pure():
    """CLAUDE.md: "`captions.py` is pure. No network, no async, no I/O."

    That purity is what makes fixture replay a complete test of this layer, so
    it is worth a guard rather than a convention. An import of `asyncio`,
    `time`, `websockets` or `pathlib` here means the seam has moved and the
    tests above stopped covering the real behaviour.
    """
    source = (ROOT / "app" / "captions.py").read_text(encoding="utf-8")
    imports = re.findall(r"^\s*(?:import|from)\s+([\w.]+)", source, re.MULTILINE)
    banned = {
        "asyncio", "time", "datetime", "websockets", "pathlib", "json",
        "socket", "requests", "httpx", "threading", "random", "os", "sys",
    }
    assert not banned.intersection(imports), (
        f"captions.py must stay pure; found {banned.intersection(imports)}"
    )
    assert "async def" not in source
    # the builtin `open`, not this module's own `_open()` paragraph helper
    assert not re.search(r"(?<![\w.])open\(", source)


# ---------------------------------------------------------------------------
# The soft break lands on a sentence end (phone test, 2026-10-13)
# ---------------------------------------------------------------------------
#
# Hamza read the captions on his phone and found the size break cutting
# mid-sentence: "...AlphaFold / at the time...". The size budget still has to
# hold, so the break now looks backwards for the last sentence end before the
# limit and only cuts blind when there is none.


def feed_words(state: CaptionState, text: str, language: str = "en") -> None:
    """Feed `text` one word per response, as space-leading final tokens.

    Space-leading because that is the only place a paragraph may be split
    (finding 1), and one word per response because that is how the soft break
    is actually reached in a live stream.
    """
    for word in text.split(" "):
        state.add_response(response(token(" " + word, lang=language)))


def closed_paragraphs(state: CaptionState, language: str) -> list[Paragraph]:
    return [p for p in state.streams[language] if p.closed and p.final.strip()]


def ends_a_sentence(text: str) -> bool:
    stripped = text.strip().rstrip(CLOSERS)
    return bool(stripped) and stripped[-1] in SENTENCE_ENDERS + CLAUSE_ENDERS


def test_size_break_cuts_at_the_last_sentence_end_before_the_limit():
    state = CaptionState(max_paragraph_chars=100)
    feed_words(state, ("word " * 14 + "stop. " + "tail " * 12).strip())

    paragraphs = state.streams["en"]
    assert len(paragraphs) > 1
    assert paragraphs[0].final.endswith("stop.")
    # The text that had already been appended past the full stop moved down
    # rather than being dropped.
    assert paragraphs[1].final.startswith("tail")


def test_size_break_prefers_the_latest_sentence_end_not_the_earliest():
    """Breaking at the first opportunity would halve every paragraph.

    The size budget was tuned against the audience page, so the cut should sit
    as close under it as a sentence end allows.
    """
    state = CaptionState(max_paragraph_chars=200)
    feed_words(
        state,
        ("word " * 12 + "early. " + "word " * 12 + "late. " + "tail " * 20).strip(),
    )
    assert state.streams["en"][0].final.endswith("late.")


def test_a_full_stop_beats_a_later_arabic_comma():
    """A paragraph that can end on a full stop should never end on a comma.

    Both candidates have to sit past the halfway floor for the preference to
    mean anything - a full stop below the floor is not a candidate at all, and
    writing this test the other way round proved it: the comma won because the
    full stop was at character 65 of a 200-character budget.
    """
    state = CaptionState(max_paragraph_chars=200)
    feed_words(
        state,
        ("word " * 26 + "stop. " + "word " * 6 + "clause، " + "tail " * 40).strip(),
    )
    assert state.streams["en"][0].final.endswith("stop.")


def test_the_arabic_comma_is_the_fallback_when_nothing_terminates():
    state = CaptionState(max_paragraph_chars=100)
    feed_words(
        state,
        ("كلمة " * 14 + "فاصلة، "
         + "ذيل " * 15).strip(),
        "ar",
    )
    assert state.streams["ar"][0].final.endswith("،")


def test_a_cut_lands_after_a_closing_quote():
    """`run5-english` reports speech as `no one has been." ` - the quote
    belongs to the sentence it closes."""
    state = CaptionState(max_paragraph_chars=100)
    feed_words(state, ("word " * 14 + 'said." ' + "tail " * 12).strip())
    assert state.streams["en"][0].final.endswith('said."')


def test_a_decimal_point_is_not_a_sentence_end():
    state = CaptionState(max_paragraph_chars=100)
    feed_words(state, ("word " * 14 + "3.5 " + "tail " * 12).strip())
    first = state.streams["en"][0]
    assert not first.final.endswith("3.")
    assert "3.5" in " ".join(p.final for p in state.streams["en"])


def test_a_sentence_end_too_early_in_the_paragraph_is_not_used():
    """A full stop five characters in would leave a five-character paragraph
    and hard-break the rest anyway - worse than the behaviour being fixed."""
    state = CaptionState(max_paragraph_chars=100)
    feed_words(state, ("hi. " + "word " * 40).strip())
    first = state.streams["en"][0]
    assert first.final != "hi."
    assert len(first.final) >= 100


def test_size_break_still_falls_back_to_a_word_boundary():
    """No terminator anywhere: the old behaviour, and still no split words."""
    state = CaptionState(max_paragraph_chars=100)
    feed_words(state, ("word " * 60).strip())
    paragraphs = closed_paragraphs(state, "en")
    assert len(paragraphs) > 1
    for para in paragraphs:
        assert para.final.endswith("word")
        assert "wor d" not in para.final


def test_the_retroactive_cut_loses_no_text():
    """The moved tail is the whole point of the risk, so pin it directly."""
    state = CaptionState(max_paragraph_chars=100)
    text = ("word " * 14 + "stop. " + "tail " * 12).strip()
    feed_words(state, text)
    rejoined = " ".join(p.final for p in state.streams["en"]).split()
    assert rejoined == text.split()


def test_a_shortened_paragraph_is_sent_in_the_diff():
    """The cut makes an already-broadcast paragraph *shorter*.

    `diff_since` sends each changed paragraph's full text keyed by `id`, so
    this needs no protocol change - but it does need the shortened paragraph
    to be in the diff, or a phone keeps the longer version for ever.
    """
    state = CaptionState(max_paragraph_chars=100)
    feed_words(state, ("word " * 14 + "stop. " + "tail " * 2).strip())
    first = state.streams["en"][0]
    assert not first.closed  # not broken yet
    long_version = first.final
    mark = state.seq

    feed_words(state, ("tail " * 12).strip())

    diff = state.diff_since(mark)
    sent = {p["id"]: p["final"] for p in diff["streams"]["en"]}
    assert first.id in sent, "the shortened paragraph was not re-sent"
    assert len(sent[first.id]) < len(long_version)
    assert sent[first.id].endswith("stop.")


@pytest.mark.parametrize("name", ALL_FIXTURES)
@pytest.mark.parametrize("language", ["ar", "en"])
def test_most_paragraphs_end_at_a_sentence_boundary(name, language):
    """The measurement that answers the phone test, on the real captures.

    Not "all": a paragraph with no terminator past the halfway floor still
    has to be cut somewhere, and `run4-saudi`'s English stream has one such
    span. Paragraphs closed by an `<end>` land wherever the speaker paused
    and are not this break's business either, which is the rest of the
    shortfall. Across the four captures the figure is 79 of 80 size-broken
    paragraphs; the floor here leaves room for the endpoint-closed ones.
    """
    state = replay(name)
    paragraphs = closed_paragraphs(state, language)
    assert paragraphs, "nothing closed; the premise of this test is gone"
    good = sum(1 for p in paragraphs if ends_a_sentence(p.final))
    assert good / len(paragraphs) >= 0.85, (
        f"{good}/{len(paragraphs)} paragraphs end at a sentence boundary: "
        + repr([p.final[-40:] for p in paragraphs if not ends_a_sentence(p.final)])
    )


@pytest.mark.parametrize("name", ALL_FIXTURES)
@pytest.mark.parametrize("language", ["ar", "en"])
def test_a_hard_break_only_happens_when_no_cut_was_available(name, language):
    """Every blind cut has to be justified, not merely rare.

    For a paragraph the size break closed, either it ends at a sentence end or
    `_sentence_cut` can find nothing in it - which is exactly the condition
    the code used. Paragraphs shorter than the budget were closed by an
    `<end>` instead and are excluded.
    """
    state = replay(name)
    for para in closed_paragraphs(state, language):
        if len(para.final) < state.max_paragraph_chars:
            continue  # closed by an endpoint, not by size
        if ends_a_sentence(para.final):
            continue
        assert state._sentence_cut(para.final) is None, (
            f"paragraph {para.id} was hard-broken but a cut existed: "
            f"{para.final[-60:]!r}"
        )


# ---------------------------------------------------------------------------
# A reconnect must not glue two sentences together (run7, 2026-10-13)
# ---------------------------------------------------------------------------
#
# The Wi-Fi drop on run7 produced "the same architecture behindMicrosoft" on
# the phone. A reconnect opens a *new* Soniox stream, and the first token of a
# stream has no leading space - it is the start of an utterance as far as the
# engine is concerned - so appending it to the text already there joined two
# words with nothing between them.


def test_a_reconnect_does_not_glue_the_next_word_on():
    """The exact run7 failure, in the smallest form that produces it."""
    state = CaptionState()
    state.add_response(response(token("the same architecture behind", lang="en")))

    state.mark_gap()  # the connection dropped here

    # A new stream: its first token carries no leading space.
    state.add_response(response(token("Microsoft", lang="en")))

    text = state.render("en")
    assert "behindMicrosoft" not in text
    assert "behind Microsoft" in text


def test_the_gap_commits_the_grey_text_as_well():
    state = CaptionState()
    state.add_response(response(token("committed", lang="en")))
    state.add_response(response(token(" and some uncommitted",
                                      final=False, lang="en")))
    assert state.streams["en"][-1].partial == " and some uncommitted"

    assert state.mark_gap() is True
    assert state.streams["en"][-1].partial == ""
    assert state.streams["en"][-1].final == "committed and some uncommitted"


def test_a_gap_with_no_partial_still_separates_the_next_token():
    """A drop between two final tokens has the same joining problem."""
    state = CaptionState()
    state.add_response(response(token("first", lang="en")))
    assert state.mark_gap() is False  # nothing grey to commit
    state.add_response(response(token("second", lang="en")))
    assert state.render("en") == "first second"


def test_the_gap_space_is_not_doubled():
    """Soniox usually *does* send a leading space; the fix must not add
    a second one when it does."""
    state = CaptionState()
    state.add_response(response(token("first", lang="en")))
    state.mark_gap()
    state.add_response(response(token(" second", lang="en")))
    assert state.render("en") == "first second"
    assert "  " not in state.render("en")


def test_the_gap_does_not_indent_a_fresh_paragraph():
    """Nothing to join to means nothing to insert."""
    state = CaptionState()
    state.mark_gap()
    state.add_response(response(token("opening", lang="en")))
    assert state.render("en") == "opening"
    assert not state.streams["en"][-1].final.startswith(" ")


def test_the_gap_is_per_language():
    """A drop interrupts both streams, and each resumes on its own token."""
    state = CaptionState()
    state.add_response(response(token("english", lang="en"),
                                token("عربي", lang="ar")))
    state.mark_gap()
    state.add_response(response(token("next", lang="en")))
    assert state.render("en") == "english next"
    # The Arabic side has not spoken yet, so its gap is still pending.
    state.add_response(response(token("تالي", lang="ar")))
    assert state.render("ar") == "عربي تالي"


def test_the_gap_clears_after_one_use():
    state = CaptionState()
    state.add_response(response(token("one", lang="en")))
    state.mark_gap()
    state.add_response(response(token("two", lang="en")))
    state.add_response(response(token("three", lang="en")))
    # Only the first token after the gap is separated; the rest keep whatever
    # spacing Soniox sent, which is how every other token is treated.
    assert state.render("en") == "one twothree"


def test_the_inserted_space_is_a_legal_break_point():
    """The size break may cut at the space the gap inserted, like any other
    word boundary - it must not produce a split word."""
    state = CaptionState(max_paragraph_chars=40)
    state.add_response(response(token("x" * 45, lang="en")))
    state.mark_gap()
    state.add_response(response(token("tail", lang="en")))
    joined = " ".join(p.final for p in state.streams["en"])
    assert "x" * 45 in joined
    assert "tail" in joined
    assert "xtail" not in joined
