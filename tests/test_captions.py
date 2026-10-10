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

from app.captions import CaptionState, Paragraph  # noqa: E402
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
