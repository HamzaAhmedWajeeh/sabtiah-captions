"""Phase 3: `app/broadcast.py` against a fake socket, offline.

No Soniox, no uvicorn, no real WebSocket. The hub only needs something with
`send_text` and `close`, which is the whole reason `ViewerSocket` is a
Protocol: a socket that can be made to block or fail on demand is the only way
to test backpressure deliberately rather than hoping to catch it on run day.

The centrepiece is `test_a_client_reconstructs_the_server_state_exactly`,
which replays a real capture through the hub into `BrowserModel` - a
deliberate re-implementation of what `web/app.js` does with snapshots and
diffs. If those two ever disagree about the protocol, that test fails rather
than a phone showing a hole in the middle of a talk.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.broadcast import BroadcastHub, PING_INTERVAL, STALL_TIMEOUT  # noqa: E402
from app.captions import CaptionState  # noqa: E402
from app.replay import responses  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


class FakeSocket:
    """A viewer socket that can be blocked or broken on purpose."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.closed: int | None = None
        self.gate: asyncio.Event | None = None
        self.broken = False

    async def send_text(self, text: str) -> None:
        if self.broken:
            raise ConnectionResetError("the phone went away")
        if self.gate is not None:
            await self.gate.wait()
        self.sent.append(text)

    async def close(self, code: int = 1000) -> None:
        self.closed = code

    # -- test helpers --

    @property
    def messages(self) -> list[dict]:
        return [json.loads(text) for text in self.sent]

    def block(self) -> asyncio.Event:
        self.gate = asyncio.Event()
        return self.gate

    def unblock(self) -> None:
        if self.gate is not None:
            self.gate.set()
            self.gate = None


class Clock:
    """A monotonic clock the test moves by hand."""

    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class BrowserModel:
    """What `web/app.js` does, in Python, so the protocol can be asserted on.

    Mirrors three behaviours exactly: a snapshot is authoritative and drops
    ids it does not mention, a diff is applied by id, and a diff whose `base`
    is ahead of what we hold means a lost message and triggers a resync.
    """

    def __init__(self) -> None:
        self.streams: dict[str, dict[int, dict]] = {"ar": {}, "en": {}}
        self.seq = 0
        self.live = False
        self.resyncs = 0
        self.pings = 0

    def receive(self, raw: str) -> None:
        message = json.loads(raw)
        kind = message.get("type")

        if kind == "ping":
            self.pings += 1
            return

        if kind == "snapshot":
            for language, paragraphs in message["streams"].items():
                keep = {p["id"]: p for p in paragraphs}
                self.streams[language] = keep
        elif kind == "diff":
            if message["base"] > self.seq:
                self.resyncs += 1
                return
            for language, paragraphs in message["streams"].items():
                for paragraph in paragraphs:
                    self.streams[language][paragraph["id"]] = paragraph
        else:
            return

        self.seq = message["seq"]
        self.live = message["live"]

    def render(self, language: str) -> str:
        """The same text `CaptionState.render` produces, assembled client-side."""
        ordered = sorted(self.streams[language].items())
        texts = [(p["final"] + p["partial"]).strip() for _, p in ordered]
        return "\n\n".join(text for text in texts if text)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def token(text, *, final=True, lang="ar", status="original", **extra):
    tok = {"text": text, "is_final": final, "translation_status": status}
    if lang is not None:
        tok["language"] = lang
    tok.update(extra)
    return tok


def response(*tokens):
    return {"tokens": list(tokens), "final_audio_proc_ms": 0}


async def settle() -> None:
    """Let every writer task run to its next await.

    The hub fills slots synchronously and the writers drain them, so a test
    that asserts on `socket.sent` has to give those tasks a turn first.
    """
    for _ in range(6):
        await asyncio.sleep(0)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def state() -> CaptionState:
    return CaptionState()


@pytest.fixture
async def hub(state, clock):
    """A hub that never runs its own loop: tests call `flush()` themselves.

    Driving `flush` directly is what makes "at most 10 per second" and the
    stall timeout testable without any real waiting.
    """
    made = BroadcastHub(state, clock=clock)
    yield made
    await made.stop()


# ---------------------------------------------------------------------------
# Connect, snapshot, diff
# ---------------------------------------------------------------------------


async def test_a_new_viewer_gets_a_full_snapshot_first(hub, state):
    state.add_response(response(token(" مرحبا")))
    socket = FakeSocket()

    viewer = await hub.add(socket)

    assert viewer is not None
    assert len(socket.messages) == 1
    first = socket.messages[0]
    assert first["type"] == "snapshot"
    assert first["seq"] == state.seq
    assert first["streams"]["ar"][0]["final"] == "مرحبا"
    # It is already up to date, so the next flush has nothing to say.
    await hub.flush()
    await settle()
    assert len(socket.messages) == 1


async def test_a_change_reaches_a_viewer_as_a_diff(hub, state):
    socket = FakeSocket()
    await hub.add(socket)

    state.add_response(response(token(" hello", lang="en")))
    await hub.flush()
    await settle()

    assert [m["type"] for m in socket.messages] == ["snapshot", "diff"]
    diff = socket.messages[1]
    assert diff["base"] == 0
    assert diff["seq"] == state.seq
    assert diff["streams"]["en"][0]["final"] == "hello"
    assert diff["streams"]["ar"] == []


async def test_several_changes_coalesce_into_one_diff(hub, state):
    """BUILD_SPEC: "coalesce diffs in between" the 10 Hz flushes."""
    socket = FakeSocket()
    await hub.add(socket)

    for word in (" one", " two", " three"):
        state.add_response(response(token(word, lang="en")))
    await hub.flush()
    await settle()

    assert len(socket.messages) == 2  # snapshot + exactly one diff
    assert socket.messages[1]["streams"]["en"][0]["final"] == "one two three"


async def test_nothing_is_sent_when_nothing_changed(hub, state):
    socket = FakeSocket()
    await hub.add(socket)

    for _ in range(5):
        await hub.flush()
    await settle()

    assert len(socket.messages) == 1


async def test_a_second_viewer_joins_mid_talk_without_disturbing_the_first(
    hub, state
):
    early = FakeSocket()
    await hub.add(early)
    state.add_response(response(token(" first", lang="en")))
    await hub.flush()
    await settle()

    late = FakeSocket()
    await hub.add(late)
    state.add_response(response(token(" second", lang="en")))
    await hub.flush()
    await settle()

    # The latecomer's snapshot already contained "first", so its diff carries
    # only the update - and both pages agree on the text.
    assert late.messages[0]["type"] == "snapshot"
    assert "first" in late.messages[0]["streams"]["en"][0]["final"]
    model_early, model_late = BrowserModel(), BrowserModel()
    for text in early.sent:
        model_early.receive(text)
    for text in late.sent:
        model_late.receive(text)
    assert model_early.render("en") == model_late.render("en") == "first second"


# ---------------------------------------------------------------------------
# The protocol as a whole
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["run1", "run5-english"])
@pytest.mark.parametrize("language", ["ar", "en"])
async def test_a_client_reconstructs_the_server_state_exactly(
    name, language, hub, state
):
    """A real capture, through the hub, into a stand-in for `web/app.js`.

    This is the Phase 3 equivalent of Phase 2's reconstruction test: not
    "did a diff arrive" but "does a page that applies snapshot-then-diffs end
    up with character-for-character what the server holds". A diff that leaks
    a paragraph, or a client that mishandles `base`, fails here.
    """
    socket = FakeSocket()
    await hub.add(socket)

    # Flush every 25 responses rather than every one, so the coalescing path
    # is the one under test - which is what the 10 Hz throttle really does.
    for index, raw in enumerate(responses(FIXTURES / f"{name}.jsonl")):
        if state.add_response(raw) and index % 25 == 0:
            await hub.flush()
            await settle()
    await hub.flush()
    await settle()

    model = BrowserModel()
    for text in socket.sent:
        model.receive(text)

    assert model.resyncs == 0
    assert model.seq == state.seq
    assert model.render(language) == state.render(language)


async def test_diffs_are_contiguous_so_a_client_never_sees_a_gap(hub, state):
    socket = FakeSocket()
    await hub.add(socket)

    for word in (" one", " two", " three"):
        state.add_response(response(token(word, lang="en")))
        await hub.flush()
        await settle()

    messages = socket.messages
    seen = messages[0]["seq"]
    for message in messages[1:]:
        # Every diff starts exactly where the last message left off. This is
        # the invariant that makes `base > seq` a reliable loss detector.
        assert message["base"] == seen
        seen = message["seq"]


async def test_a_client_that_misses_a_message_asks_for_a_resync(hub, state):
    """The `base` check in `web/app.js`, exercised by dropping a message."""
    socket = FakeSocket()
    await hub.add(socket)
    for word in (" one", " two"):
        state.add_response(response(token(word, lang="en")))
        await hub.flush()
        await settle()

    model = BrowserModel()
    model.receive(socket.sent[0])     # snapshot
    model.receive(socket.sent[2])     # second diff; the first never arrived

    assert model.resyncs == 1
    assert model.render("en") == ""   # a hole is refused, not painted


# ---------------------------------------------------------------------------
# Resync
# ---------------------------------------------------------------------------


async def test_resync_answers_with_a_snapshot(hub, state):
    socket = FakeSocket()
    viewer = await hub.add(socket)
    state.add_response(response(token(" hello", lang="en")))
    await hub.flush()
    await settle()

    hub.resync(viewer)
    await hub.flush()
    await settle()

    assert [m["type"] for m in socket.messages] == ["snapshot", "diff", "snapshot"]
    assert socket.messages[-1]["streams"]["en"][0]["final"] == "hello"


async def test_repeated_resyncs_collapse_into_one_snapshot(hub, state):
    socket = FakeSocket()
    viewer = await hub.add(socket)

    for _ in range(10):
        hub.resync(viewer)
    await hub.flush()
    await settle()

    assert [m["type"] for m in socket.messages] == ["snapshot", "snapshot"]


async def test_rebinding_a_new_session_resyncs_every_viewer(hub):
    """Starting a second talk must not leave pages showing the first one."""
    socket = FakeSocket()
    await hub.add(socket)
    hub.state.add_response(response(token(" first talk", lang="en")))
    await hub.flush()
    await settle()

    fresh = CaptionState()
    hub.rebind(fresh)
    fresh.add_response(response(token(" second talk", lang="en")))
    await hub.flush()
    await settle()

    assert socket.messages[-1]["type"] == "snapshot"
    model = BrowserModel()
    for text in socket.sent:
        model.receive(text)
    # The first talk is gone, not appended to.
    assert model.render("en") == "second talk"


# ---------------------------------------------------------------------------
# Backpressure
# ---------------------------------------------------------------------------


async def test_a_busy_viewer_gets_one_wider_diff_not_a_backlog(hub, state):
    """BUILD_SPEC: "don't let one slow phone stall everyone"."""
    socket = FakeSocket()
    viewer = await hub.add(socket)
    gate = socket.block()

    state.add_response(response(token(" one", lang="en")))
    await hub.flush()
    await settle()
    assert viewer.busy                       # the diff is stuck in the socket

    state.add_response(response(token(" two", lang="en")))
    state.add_response(response(token(" three", lang="en")))
    await hub.flush()                        # offered and refused, not queued
    await settle()
    assert len(socket.sent) == 1             # still only the snapshot

    gate.set()
    await settle()
    await hub.flush()
    await settle()

    # Two diffs in total, not three: the second covers everything the viewer
    # missed while it was busy.
    assert [m["type"] for m in socket.messages] == ["snapshot", "diff", "diff"]
    assert socket.messages[-1]["streams"]["en"][0]["final"] == "one two three"


async def test_one_slow_viewer_does_not_delay_the_others(hub, state):
    slow, quick = FakeSocket(), FakeSocket()
    await hub.add(slow)
    await hub.add(quick)
    slow.block()

    state.add_response(response(token(" hello", lang="en")))
    await hub.flush()
    await settle()

    assert len(quick.messages) == 2          # snapshot + diff, unaffected
    assert len(slow.messages) == 1


async def test_a_stalled_viewer_is_dropped(hub, state, clock):
    socket = FakeSocket()
    await hub.add(socket)
    socket.block()

    state.add_response(response(token(" one", lang="en")))
    await hub.flush()                        # accepted, now stuck mid-write
    await settle()

    state.add_response(response(token(" two", lang="en")))
    await hub.flush()                        # refused: the stall clock starts
    await settle()
    assert hub.viewer_count == 1

    clock.advance(STALL_TIMEOUT + 1)
    state.add_response(response(token(" three", lang="en")))
    await hub.flush()
    await settle()

    assert hub.viewer_count == 0
    assert hub.dropped == 1
    assert socket.closed == 1013             # "try again later", so it will


async def test_a_viewer_that_catches_up_is_not_dropped(hub, state, clock):
    socket = FakeSocket()
    await hub.add(socket)
    gate = socket.block()

    state.add_response(response(token(" one", lang="en")))
    await hub.flush()
    await settle()
    state.add_response(response(token(" two", lang="en")))
    await hub.flush()                        # refused once
    await settle()

    gate.set()
    await settle()
    clock.advance(STALL_TIMEOUT + 1)         # late, but it is keeping up now
    await hub.flush()
    await settle()

    assert hub.viewer_count == 1
    assert hub.dropped == 0


async def test_a_dead_socket_is_dropped_without_retrying(hub, state):
    socket = FakeSocket()
    await hub.add(socket)
    socket.broken = True

    state.add_response(response(token(" one", lang="en")))
    await hub.flush()
    await settle()
    await hub.flush()                        # notices the writer gave up
    await settle()

    assert hub.viewer_count == 0


async def test_the_viewer_cap_refuses_extra_sockets(state, clock):
    hub = BroadcastHub(state, max_viewers=2, clock=clock)
    try:
        first, second = FakeSocket(), FakeSocket()
        assert await hub.add(first) is not None
        assert await hub.add(second) is not None

        third = FakeSocket()
        assert await hub.add(third) is None
        assert hub.viewer_count == 2
        assert hub.rejected == 1
        assert third.closed == 1013
        assert third.sent == []
    finally:
        await hub.stop()


async def test_a_socket_that_dies_on_its_snapshot_is_never_registered(hub):
    socket = FakeSocket()
    socket.broken = True

    assert await hub.add(socket) is None
    assert hub.viewer_count == 0
    assert socket.closed == 1013


# ---------------------------------------------------------------------------
# Pings and trimming
# ---------------------------------------------------------------------------


async def test_a_ping_goes_out_on_an_idle_connection(hub, clock):
    socket = FakeSocket()
    await hub.add(socket)

    await hub.flush()
    await settle()
    assert len(socket.messages) == 1          # nothing due yet

    clock.advance(PING_INTERVAL + 1)
    await hub.flush()
    await settle()

    assert socket.messages[-1] == {"type": "ping"}


async def test_a_ping_does_not_advance_the_client(hub, state, clock):
    """A ping says the path is alive, nothing about caption state."""
    socket = FakeSocket()
    viewer = await hub.add(socket)
    clock.advance(PING_INTERVAL + 1)
    await hub.flush()
    await settle()
    assert viewer.last_seq == 0

    state.add_response(response(token(" hello", lang="en")))
    await hub.flush()
    await settle()

    diff = socket.messages[-1]
    assert diff["type"] == "diff"
    assert diff["base"] == 0                  # not skipped past by the ping
    assert diff["streams"]["en"][0]["final"] == "hello"


async def test_captions_outrank_a_due_ping(hub, state, clock):
    socket = FakeSocket()
    await hub.add(socket)
    clock.advance(PING_INTERVAL + 1)
    state.add_response(response(token(" hello", lang="en")))

    await hub.flush()
    await settle()

    assert socket.messages[-1]["type"] == "diff"


async def test_a_client_drops_paragraphs_the_cap_trimmed(clock):
    """`captions.py`: "trimming is invisible in a diff" - so prove a page copes.

    The cap drops the oldest paragraphs with no "deleted" message of any kind.
    A page that only ever applied diffs would hold them forever and slowly
    diverge; the snapshot is what corrects it.
    """
    state = CaptionState(max_paragraphs=3, max_paragraph_chars=1)
    hub = BroadcastHub(state, clock=clock)
    try:
        socket = FakeSocket()
        viewer = await hub.add(socket)
        model = BrowserModel()

        for word in (" one", " two", " three", " four", " five"):
            state.add_response(response(token(word, lang="en")))
            await hub.flush()
            await settle()
        for text in socket.sent:
            model.receive(text)

        # Diffs alone cannot shed the trimmed paragraphs.
        assert len(model.streams["en"]) > len(state.streams["en"])

        hub.resync(viewer)
        await hub.flush()
        await settle()
        model.receive(socket.sent[-1])

        assert len(model.streams["en"]) == len(state.streams["en"]) == 3
        assert model.render("en") == state.render("en")
    finally:
        await hub.stop()


async def test_status_reports_counters_and_no_caption_text(hub, state):
    socket = FakeSocket()
    await hub.add(socket)
    state.add_response(response(token(" secret words", lang="en")))
    state.set_live(True)

    status = hub.status()

    assert status["viewers"] == 1
    assert status["live"] is True
    assert status["seq"] == state.seq
    assert "secret" not in json.dumps(status)


async def test_stop_closes_every_viewer(hub, state):
    sockets = [FakeSocket() for _ in range(3)]
    for socket in sockets:
        await hub.add(socket)

    await hub.stop()

    assert hub.viewer_count == 0
    assert all(socket.closed == 1013 for socket in sockets)


async def test_arabic_is_not_escaped_on_the_wire(hub, state):
    """`ensure_ascii=False`: escaping would roughly triple every Arabic byte."""
    socket = FakeSocket()
    await hub.add(socket)
    state.add_response(response(token(" مرحبا بالعالم")))
    await hub.flush()
    await settle()

    assert "مرحبا" in socket.sent[-1]
    assert "\\u" not in socket.sent[-1]
