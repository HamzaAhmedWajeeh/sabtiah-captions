"""Caption state: Soniox responses in, serializable snapshot out.

Pure by design - no network, no async, no I/O, no clock. That is what makes
fixture replay a complete test of this layer: `tests/test_captions.py` drives it
from the real captures in `tests/fixtures/` and never touches Soniox.

Design is BUILD_SPEC section 6's **Option B**: two rolling streams, `ar` and
`en`, grouped by *output* language. The `en` stream is everything in English -
English speech plus translations of Arabic speech - and `ar` is the mirror. A
listener picks a language, not a side.

Five things measured in Phase 1 shape this file; see docs/soniox-notes.md.

1. **Tokens are sub-word pieces.** A word is a run of tokens, and a word
   *boundary* is marked by a leading space on the next token (1,579 of 3,283
   final tokens in run1 start with a space; exactly one ends with one). So a
   paragraph may only be split immediately before a space-leading token -
   splitting "at a token boundary" naively cuts words in half.
2. **`<end>` carries `translation_status: "none"`, not `"original"`**, and has
   no `language`, no `start_ms`, no `end_ms`. Anything that selects original
   tokens by `== "original"` silently drops every paragraph boundary, so the
   test here is "not translation".
3. **Endpoints can effectively never arrive.** `run4-saudi` yielded one `<end>`
   in 300 s - a single 3,974-character segment. `<end>` alone therefore cannot
   drive paragraph breaks, so a soft size break backs it up. This is a
   deliberate deviation from the spec's wording, recorded in the notes.
4. **Translation tokens are always final** (4,493 of 4,493 across five
   captures). The translated side of a stream has no partial text, so a
   translated paragraph's `partial` stays empty. Non-final translations are
   still handled, in case that ever changes.
5. **`language` is reliable at the utterance level.** Embedded English inside
   Arabic speech is tagged `ar`, and a genuinely English turn is tagged `en`,
   so routing on `language` is correct and the spec's "majority language" rule
   is unnecessary.
"""

from __future__ import annotations

from dataclasses import dataclass

END_TOKEN = "<end>"
TRANSLATION = "translation"

#: Soft paragraph break. Endpoint detection cannot be relied on (finding 3), so
#: a paragraph is cut once it passes this many characters, at the next word
#: boundary. Roughly a few lines on a phone at the audience page's base size.
MAX_PARAGRAPH_CHARS = 360

#: BUILD_SPEC section 6: "keep the last 200 segments". Counted per stream, and
#: counted *after* soft splitting, or a single endpoint-free talk would sit
#: forever in one uncapped paragraph.
MAX_PARAGRAPHS = 200


@dataclass
class Paragraph:
    """One block of caption text in one language.

    `final` only ever grows; `partial` is replaced wholesale on every response
    and is the text Soniox has not committed to yet.
    """

    id: int
    final: str = ""
    partial: str = ""
    changed_seq: int = 0
    closed: bool = False

    def as_dict(self) -> dict:
        return {"id": self.id, "final": self.final, "partial": self.partial}

    @property
    def empty(self) -> bool:
        return not self.final and not self.partial


class CaptionState:
    """Accumulates Soniox responses into per-language paragraph streams.

    Two contract details Phase 3's broadcast hub depends on, both deviating
    mildly from the snapshot example in BUILD_SPEC section 6:

    - **Paragraph ids are globally unique, not aligned across streams.** The
      spec's example shows id 311 in both `en` and `ar`, which implies the two
      sides break together. Under Option B they are independent streams and
      break at different points - different languages reach the size budget
      after different amounts of text - so a shared id would be a lie. Ids come
      from one counter, so an id identifies a paragraph *and* its stream.
    - **Trimming is invisible in a diff.** When the cap drops the oldest
      paragraphs they simply stop appearing; a diff never says "deleted". A
      client must treat a full snapshot as authoritative and drop any id absent
      from it, which it already has to do after a `seq` gap.
    """

    def __init__(
        self,
        languages: tuple[str, ...] = ("ar", "en"),
        max_paragraph_chars: int = MAX_PARAGRAPH_CHARS,
        max_paragraphs: int = MAX_PARAGRAPHS,
    ) -> None:
        self.languages = tuple(languages)
        self.max_paragraph_chars = max_paragraph_chars
        self.max_paragraphs = max_paragraphs
        self.streams: dict[str, list[Paragraph]] = {
            lang: [] for lang in self.languages
        }
        self.seq = 0
        self.live = False
        self._next_id = 1
        # An endpoint closes the original paragraph immediately, but the
        # translated side has no end marker of its own (Phase 1 question 2), so
        # the break is deferred until its next token arrives.
        self._pending_endpoints: dict[str, int] = {
            lang: 0 for lang in self.languages
        }
        self._last_original_language: str | None = None

    # -- public API --------------------------------------------------------

    def set_live(self, live: bool) -> None:
        if live != self.live:
            self.live = live
            self.seq += 1

    def add_response(self, response: dict) -> bool:
        """Fold one Soniox response in. True if anything a viewer sees changed.

        Error responses carry `tokens: []` alongside `error_type` (Phase 1), so
        an empty token list is a no-op rather than a reason to clear anything.
        """
        tokens = response.get("tokens") or []
        if not tokens:
            return False

        seq = self.seq + 1
        changed = False
        # Non-final tokens are re-sent in full on every response, so the
        # partial buffer is rebuilt from this response alone and replaces
        # whatever was there - appending them is what duplicates text.
        partials: dict[str, str] = {lang: "" for lang in self.languages}

        for token in tokens:
            text = token.get("text", "")
            is_translation = token.get("translation_status") == TRANSLATION

            if text == END_TOKEN:
                # Never displayed; it is a boundary. Finding 2: test "not
                # translation" rather than == "original".
                if not is_translation and self._close_original(seq):
                    changed = True
                continue

            if not text:
                continue

            lang = self._route(token, is_translation)
            if lang is None:
                continue
            if not is_translation:
                self._last_original_language = lang

            if token.get("is_final"):
                self._append_final(lang, text, seq, is_translation)
                changed = True
            else:
                partials[lang] += text

        if self._apply_partials(partials, seq):
            changed = True

        if changed:
            self.seq = seq
            self._trim()
        return changed

    def snapshot(self) -> dict:
        """Everything, for a client that has just connected or resynced."""
        return {
            "type": "snapshot",
            "seq": self.seq,
            "live": self.live,
            "streams": {
                lang: [p.as_dict() for p in paras]
                for lang, paras in self.streams.items()
            },
        }

    def diff_since(self, seq: int) -> dict:
        """Only the paragraphs that changed after `seq`, keyed by `id`."""
        return {
            "type": "diff",
            "seq": self.seq,
            "live": self.live,
            "streams": {
                lang: [p.as_dict() for p in paras if p.changed_seq > seq]
                for lang, paras in self.streams.items()
            },
        }

    def render(self, language: str) -> str:
        """Plain text of one stream. For eyeballing a fixture, and for tests."""
        return "\n\n".join(
            (p.final + p.partial).strip()
            for p in self.streams[language]
            if (p.final + p.partial).strip()
        )

    # -- internals ---------------------------------------------------------

    def _route(self, token: dict, is_translation: bool) -> str | None:
        """Which output stream this token belongs in.

        A translation token's `language` is its *target*, which is exactly the
        output stream - "everything in English" includes translations into
        English. Originals route on their own `language`.
        """
        lang = token.get("language")
        if lang in self.streams:
            return lang

        if is_translation:
            # No target language: infer it as "the one that isn't the source".
            source = token.get("source_language")
            if source in self.streams:
                return self._other(source)
            return None

        # An original token with no usable language (only `<end>` should hit
        # this, and it is handled before routing) follows the last known one.
        return self._last_original_language

    def _other(self, language: str) -> str | None:
        for lang in self.languages:
            if lang != language:
                return lang
        return None

    def _tail(self, language: str) -> Paragraph | None:
        paras = self.streams[language]
        return paras[-1] if paras else None

    def _open(self, language: str, seq: int) -> Paragraph:
        """The paragraph currently accepting text, creating one if needed."""
        tail = self._tail(language)
        if tail is not None and not tail.closed:
            return tail
        para = Paragraph(id=self._next_id, changed_seq=seq)
        self._next_id += 1
        self.streams[language].append(para)
        return para

    def _close(self, language: str, seq: int) -> bool:
        """Finish the open paragraph. True if there was one worth closing."""
        tail = self._tail(language)
        if tail is None or tail.closed or tail.empty:
            return False
        tail.closed = True
        if tail.partial:
            # At an endpoint every token of the segment has already arrived as
            # a final, so a leftover partial is speculative text for the *next*
            # segment. Dropping it costs nothing: non-finals are re-sent in
            # full on the very next response.
            tail.partial = ""
        tail.changed_seq = seq
        return True

    def _close_original(self, seq: int) -> bool:
        """Handle an `<end>`: close the original side, arm the translated one.

        `<end>` carries no `language` (finding 2), so the stream it belongs to
        is the last original language seen.
        """
        language = self._last_original_language
        if language is None:
            return False
        closed = self._close(language, seq)
        other = self._other(language)
        if other is not None:
            self._pending_endpoints[other] += 1
        return closed

    def _append_final(
        self, language: str, text: str, seq: int, is_translation: bool
    ) -> None:
        if is_translation and self._pending_endpoints[language] > 0:
            # The deferred break from an earlier `<end>`, taken now that the
            # translated side has produced a token since.
            #
            # It can only be taken at a word boundary. Translation tokens are
            # sub-word pieces like the originals, and an endpoint lands wherever
            # the speaker paused - which is mid-word more often than not. Taking
            # the break immediately turned "Meaning" into "Me aning" in run1.
            tail = self._tail(language)
            at_word_start = text.startswith(" ")
            paragraph_empty = tail is None or tail.closed or not tail.final
            if at_word_start or paragraph_empty:
                self._pending_endpoints[language] -= 1
                if at_word_start:
                    self._close(language, seq)

        para = self._open(language, seq)

        # Soft size break (finding 3). Only legal immediately before a
        # space-leading token, because tokens are sub-word pieces (finding 1)
        # and cutting anywhere else splits a word.
        if (
            para.final
            and len(para.final) >= self.max_paragraph_chars
            and text.startswith(" ")
        ):
            self._close(language, seq)
            para = self._open(language, seq)

        if not para.final:
            text = text.lstrip()
            if not text:
                return

        para.final += text
        para.changed_seq = seq

    def _apply_partials(self, partials: dict[str, str], seq: int) -> bool:
        changed = False
        for language, text in partials.items():
            if text:
                para = self._open(language, seq)
                if not para.final:
                    text = text.lstrip()
                if para.partial != text:
                    para.partial = text
                    para.changed_seq = seq
                    changed = True
            else:
                tail = self._tail(language)
                if tail is not None and tail.partial:
                    tail.partial = ""
                    tail.changed_seq = seq
                    changed = True
        return changed

    def _trim(self) -> None:
        for paras in self.streams.values():
            excess = len(paras) - self.max_paragraphs
            if excess > 0:
                del paras[:excess]
