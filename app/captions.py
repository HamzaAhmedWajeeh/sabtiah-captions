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

#: Sentence terminators the soft break prefers to cut at, so a paragraph does
#: not end mid-sentence ("...AlphaFold / at the time..." on the phone test of
#: 2026-10-13). Arabic uses `.` as a full stop and `؟` as a question mark.
SENTENCE_ENDERS = ".?!؟"

#: A weaker fallback, used only when a paragraph holds no sentence terminator
#: at all. The Arabic comma is a much stronger pause than the English one -
#: it often separates clauses that stand alone - and it is the only comma in
#: this set on purpose: `,` is so frequent in the English stream (87 of them
#: in run1 against 59 full stops) that allowing it would cut most paragraphs
#: at a comma instead of a sentence end.
CLAUSE_ENDERS = "،"

#: Shown in place of speech the audience never received, one per stream.
#: Both languages say the same thing, because a viewer reading Arabic needs
#: to know a gap happened just as much as one reading English.
NOTICE_TEXT = {
    "en": "[connection interrupted]",
    "ar": "[انقطع الاتصال]",
}

#: Characters a sentence may legitimately close with *after* its terminator.
#: A cut lands after these, so reported speech is not split from its own
#: closing quote.
CLOSERS = "\"')]»”’"

#: How far into a paragraph a cut must land to be worth taking, as a fraction
#: of the size budget. Without this, a full stop five characters in would
#: produce a five-character paragraph and leave the rest to be hard-broken
#: anyway, which is worse than the behaviour being fixed.
MIN_BREAK_FRACTION = 0.5

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
    #: A message from the system rather than something anyone said. The page
    #: styles these differently so they are not mistaken for speech.
    notice: bool = False

    def as_dict(self) -> dict:
        out = {"id": self.id, "final": self.final, "partial": self.partial}
        if self.notice:
            # Only when set: it is rare, and every other paragraph would
            # otherwise carry a field that is false on all of them.
            out["notice"] = True
        return out

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
        #: The engine's own health, which is *not* `live`. BUILD_SPEC Phase 5:
        #: "Mark the stream `live:false` only when the operator stops it, not
        #: during reconnects." A page that went grey every time the socket to
        #: Soniox blinked would look broken during the one event this project
        #: is designed to survive, so a drop changes this instead and the page
        #: keeps the captions it has with a small indicator.
        #: One of `idle`, `connecting`, `live`, `reconnecting`, `paused`.
        self.status = "idle"
        self._next_id = 1
        # An endpoint closes the original paragraph immediately, but the
        # translated side has no end marker of its own (Phase 1 question 2), so
        # the break is deferred until its next token arrives.
        self._pending_endpoints: dict[str, int] = {
            lang: 0 for lang in self.languages
        }
        self._last_original_language: str | None = None
        #: Set by `mark_gap` when the feed was interrupted; cleared by the
        #: next final token, which must not be glued onto the text before it.
        self._gap: dict[str, bool] = {lang: False for lang in self.languages}

    # -- public API --------------------------------------------------------

    def set_live(self, live: bool) -> None:
        if live != self.live:
            self.live = live
            self.seq += 1

    def set_status(self, status: str) -> bool:
        """Record the engine's health. True if it changed.

        Separate from `set_live` on purpose - see the note on `self.status`.
        """
        if status == self.status:
            return False
        self.status = status
        self.seq += 1
        return True

    def mark_gap(self) -> bool:
        """The audio feed was interrupted. True if anything a viewer sees changed.

        Does two things, and the second one is the reason this exists rather
        than a bare `finalize_partials`:

        1. Commits the open partials, so no grey line hangs for the length of
           a reconnect.
        2. Records that the *next* final token in each stream begins a new
           utterance. A reconnect opens a fresh Soniox stream, and the first
           token of a stream has no leading space - it is the start of a
           sentence as far as the engine is concerned. Appending it to text
           that is already there produced `behindMicrosoft` on run7
           (2026-10-13). A word boundary is inserted instead.

        The gap is marked even when there was no partial to commit: a drop
        between two final tokens has exactly the same joining problem.
        """
        changed = self.finalize_partials()
        for language in self.languages:
            tail = self._tail(language)
            if tail is not None and tail.final and not tail.closed:
                self._gap[language] = True
        return changed

    def add_notice(self, texts: dict[str, str] | None = None) -> bool:
        """Mark a gap the audience should know about. Always changes something.

        Used when audio was captured and then thrown away - a reconnect that
        outlived the replay window (BUILD_SPEC Phase 5, and the decision of
        2026-10-13 to stay live rather than catch up). Without it the captions
        simply jump, and a reader has no way to tell a gap from a speaker
        changing the subject.

        The notice closes whatever was open and is itself closed, so the text
        that follows starts cleanly rather than continuing a sentence that
        lost its middle.
        """
        texts = texts or NOTICE_TEXT
        self.seq += 1
        for language in self.languages:
            tail = self._tail(language)
            if tail is not None and not tail.closed:
                tail.closed = True
                tail.partial = ""
                tail.changed_seq = self.seq
            para = Paragraph(id=self._next_id, changed_seq=self.seq,
                             notice=True, closed=True)
            self._next_id += 1
            para.final = texts.get(language, NOTICE_TEXT.get(language, ""))
            self.streams[language].append(para)
            # The gap flag is irrelevant now: the next token opens its own
            # paragraph, so there is nothing to be glued onto.
            self._gap[language] = False
        self._trim()
        return True

    def finalize_partials(self) -> bool:
        """Commit every open partial as final text. True if anything changed.

        Called when the engine connection drops (BUILD_SPEC Phase 5). A
        partial is text Soniox had not committed to, drawn grey on the page,
        and it is only ever cleared by the *next* response - so after a drop
        it would sit there grey for as long as the reconnect takes, and if the
        reconnect replays audio from before it, arrive a second time.

        Committing it as-is is the honest option: it is what the audience has
        already read, and the alternative is deleting words from the screen.
        """
        changed = False
        for language in self.languages:
            tail = self._tail(language)
            if tail is None or not tail.partial:
                continue
            self.seq += 1
            tail.final += tail.partial
            tail.partial = ""
            tail.changed_seq = self.seq
            changed = True
        return changed

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
            "status": self.status,
            "streams": {
                lang: [p.as_dict() for p in paras]
                for lang, paras in self.streams.items()
            },
        }

    def diff_since(self, seq: int) -> dict:
        """Only the paragraphs that changed after `seq`, keyed by `id`.

        `base` is the sequence number this diff was computed *from*, which is
        what makes BUILD_SPEC's "a client that sees a `seq` gap requests a
        full snapshot" implementable: without it a client can see that a diff
        is newer than its own state but not whether anything fell out in
        between. A client resyncs when `base > its own seq`.
        """
        return {
            "type": "diff",
            "seq": self.seq,
            "base": seq,
            "live": self.live,
            "status": self.status,
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

    def _sentence_cut(self, final: str) -> int | None:
        """Where to cut `final` so the paragraph ends at a sentence end.

        Returns an index to split at - everything before it stays, everything
        from it moves on - or None if there is nothing better than a hard cut.

        Searches backwards from the end, so the cut is the *last* sentence end
        before the size limit rather than the first one past some minimum.
        That keeps paragraphs close to the budget the audience page was tuned
        against; breaking at the earliest opportunity would make every
        paragraph about half the intended size.

        A terminator only counts when a space follows it, possibly through a
        closing quote or bracket - `run5-english` has reported speech ending
        `no one has been." ` and the cut belongs after the quote, not before
        it. Requiring the space is what keeps "3.5" and "stt-rt-v5." apart,
        and it is cheap. It does not save "Dr. Smith" - an abbreviation is
        indistinguishable from a sentence end without a dictionary, and the
        cost of getting that wrong is a paragraph that breaks one word early,
        not a word cut in half.
        """
        floor = int(self.max_paragraph_chars * MIN_BREAK_FRACTION)
        if len(final) <= floor:
            return None
        # Strong terminators first, and only then the clause fallback: a
        # paragraph that can end on a full stop should never end on a comma.
        for enders in (SENTENCE_ENDERS, CLAUSE_ENDERS):
            for index in range(len(final) - 1, floor - 1, -1):
                if final[index] not in enders:
                    continue
                after = index + 1
                while (after < len(final) and after - index <= len(CLOSERS)
                       and final[after] in CLOSERS):
                    after += 1
                if final[after:after + 1] == " ":
                    return after
        return None

    def _break(self, language: str, para: "Paragraph", seq: int) -> None:
        """Close `para` at a sentence end if it has one, otherwise as-is.

        The cut is retroactive: the text after the last sentence end has
        already been appended, and is moved into the new paragraph. That is
        visible on the page as the last line or so of a block dropping into
        the block below, once per paragraph. It is a deliberate trade for
        never ending a block mid-sentence, and it costs nothing in the
        protocol - a diff carries each paragraph's full text keyed by `id`
        (`diff_since`), so a paragraph getting *shorter* is an ordinary
        update, not a special case.
        """
        cut = self._sentence_cut(para.final)
        carry = ""
        if cut is not None:
            carry = para.final[cut:].lstrip()
            para.final = para.final[:cut]
        self._close(language, seq)
        if carry:
            moved = self._open(language, seq)
            moved.final = carry
            moved.changed_seq = seq

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

        # A reconnect opened a new stream, so this token starts an utterance
        # and carries no leading space of its own (run7, `behindMicrosoft`).
        # Done before the size break so the inserted space is a legal place
        # to cut, exactly like any other word boundary.
        if self._gap.get(language):
            self._gap[language] = False
            if para.final and not text.startswith(" ")                     and not para.final.endswith(" "):
                text = " " + text

        # Soft size break (finding 3). Only legal immediately before a
        # space-leading token, because tokens are sub-word pieces (finding 1)
        # and cutting anywhere else splits a word.
        if (
            para.final
            and len(para.final) >= self.max_paragraph_chars
            and text.startswith(" ")
        ):
            self._break(language, para, seq)
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
