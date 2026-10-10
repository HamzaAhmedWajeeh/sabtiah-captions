"""Locks the arithmetic in tools/probe_soniox.py's analyze mode.

The capture built here is synthetic: the token shapes follow the documented
Soniox response format, but the timings are invented. This test proves the
analyzer reports the right numbers for a known input — it says nothing about
how Soniox actually behaves. The real answers come from a live capture
(see docs/soniox-notes.md).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import probe_soniox as P  # noqa: E402


def _orig(text, final, start, end, lang):
    return {
        "text": text, "is_final": final, "confidence": 0.9,
        "start_ms": start, "end_ms": end, "language": lang,
        "translation_status": "original",
    }


def _tr(text, lang, src):
    return {
        "text": text, "is_final": True, "confidence": 0.9,
        "language": lang, "source_language": src,
        "translation_status": "translation",
    }


def _end(lang):
    return {"text": "<end>", "is_final": True, "language": lang,
            "translation_status": "original"}


# (recv_offset_ms, tokens) — three segments with known translation lateness.
CAPTURE: list[tuple[int, list[dict]]] = [
    # Segment 0: Arabic with English tech terms; translation tail is 800 ms late
    (900, [_orig("اليوم", True, 100, 700, "ar"),
           _orig(" بنشرح", False, 700, 1100, "ar")]),
    (1400, [_orig(" بنشرح", True, 700, 1100, "ar"),
            _orig(" RAG", False, 1100, 1500, "en")]),
    (1900, [_orig(" RAG", True, 1100, 1500, "en"),
            _orig(" and agentic", False, 1500, 2200, "en")]),
    (2600, [_orig(" and agentic", True, 1500, 2200, "en"),
            _orig(" workflows", True, 2200, 2600, "en"),
            _end("ar"),
            _tr("Today we'll explain RAG", "en", "ar")]),
    (3400, [_tr(" and agentic workflows", "en", "ar")]),
    # Segment 1: English in, Arabic out, translation lands with the <end>
    (4200, [_orig("The model", True, 3000, 3800, "en"),
            _orig(" is small", False, 3800, 4200, "en")]),
    (4800, [_orig(" is small", True, 3800, 4400, "en"),
            _end("en"),
            _tr("النموذج صغير", "ar", "en")]),
    # Segment 2: both translation pieces land after <end>
    (6000, [_orig("شكرا لكم", True, 5000, 5800, "ar"), _end("ar")]),
    (6500, [_tr("Thank you", "en", "ar")]),
    (7200, [_tr(" all", "en", "ar")]),
]


@pytest.fixture
def capture(tmp_path: Path) -> Path:
    path = tmp_path / "synthetic.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps({"meta": {"audio": "synthetic.wav", "chunk_ms": 100}}) + "\n")
        for recv_ms, tokens in CAPTURE:
            fh.write(json.dumps({
                "recv_at": "2026-10-10T20:00:00+00:00",
                "recv_offset_ms": recv_ms,
                "sent_ms": recv_ms,
                "response": {"tokens": tokens,
                             "final_audio_proc_ms": recv_ms,
                             "total_audio_proc_ms": recv_ms},
            }, ensure_ascii=False) + "\n")
        fh.write(json.dumps({
            "recv_at": "2026-10-10T20:00:10+00:00", "recv_offset_ms": 7400,
            "sent_ms": 7400, "response": {"finished": True},
        }) + "\n")
    return path


def test_segments_are_bounded_by_end_tokens(capture: Path):
    _, records = P.load_capture(capture)
    segments, errors = P.build_segments(records)
    assert not errors
    assert len(segments) == 3
    assert segments[0].orig_text() == "اليوم بنشرح RAG and agentic workflows"
    assert segments[1].orig_text() == "The model is small"
    assert segments[2].orig_text() == "شكرا لكم"


def test_end_token_never_leaks_into_text(capture: Path):
    _, records = P.load_capture(capture)
    segments, _ = P.build_segments(records)
    for segment in segments:
        assert P.END_TOKEN not in segment.orig_text()
        assert P.END_TOKEN not in segment.tr_text()


def test_non_final_tokens_are_not_counted_as_evidence(capture: Path):
    """Non-finals are re-sent in full; counting them would duplicate text."""
    _, records = P.load_capture(capture)
    segments, _ = P.build_segments(records)
    # " بنشرح" and " RAG" each appear as non-final then final.
    assert segments[0].orig_text().count("بنشرح") == 1
    assert segments[0].orig_text().count("RAG") == 1


def test_late_translation_attaches_to_the_closed_segment(capture: Path):
    """A translation token arriving after <end> belongs to the segment before it."""
    _, records = P.load_capture(capture)
    segments, _ = P.build_segments(records)
    assert segments[0].tr_text() == "Today we'll explain RAG and agentic workflows"
    assert segments[2].tr_text() == "Thank you all"


def test_translation_lateness_relative_to_end(capture: Path):
    _, records = P.load_capture(capture)
    segments, _ = P.build_segments(records)
    lateness = []
    for segment in segments:
        after = [t for t in segment.translation
                 if t.recv_offset_ms > segment.end_recv_ms]
        lateness.append(max((t.recv_offset_ms for t in after), default=0)
                        - (segment.end_recv_ms if after else 0))
    assert lateness == [800, 0, 1200]


def test_source_language_pairs(capture: Path):
    _, records = P.load_capture(capture)
    segments, _ = P.build_segments(records)
    pairs = sorted(
        (t.source_language, t.language)
        for s in segments for t in s.translation
    )
    # four ar->en tokens (segments 0 and 2, two pieces each), one en->ar
    assert pairs == [("ar", "en")] * 4 + [("en", "ar")]


def test_mixed_script_detection(capture: Path):
    _, records = P.load_capture(capture)
    segments, _ = P.build_segments(records)
    assert P.script_mix(segments[0].orig_text()) == "mixed"
    assert P.script_mix(segments[1].orig_text()) == "latin"
    assert P.script_mix(segments[2].orig_text()) == "arabic"


def test_latin_tokens_inside_arabic_segment_keep_their_language(capture: Path):
    _, records = P.load_capture(capture)
    segments, _ = P.build_segments(records)
    latin = [t for t in segments[0].orig if P.LATIN.search(t.text)]
    assert [t.language for t in latin] == ["en", "en", "en"]


def test_translation_lag_against_last_spoken_word(capture: Path):
    _, records = P.load_capture(capture)
    segments, _ = P.build_segments(records)
    first, last = [], []
    for segment in segments:
        end_ms = segment.last_orig_end_ms()
        first.append(min(t.recv_offset_ms for t in segment.translation) - end_ms)
        last.append(max(t.recv_offset_ms for t in segment.translation) - end_ms)
    assert first == [0, 400, 700]
    assert last == [800, 400, 1400]


def test_summarize_renders_all_five_questions(capture: Path):
    report = P.summarize(capture)
    for heading in ("## Q1.", "## Q2.", "## Q3.", "## Q4.", "## Q5."):
        assert heading in report
    assert "**2 of 3**" in report  # segments whose translation finished late
    assert "`<end>` tokens carrying" in report
