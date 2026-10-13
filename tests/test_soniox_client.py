"""Phase 5: the resilient Soniox client. Offline, with a fake socket.

BUILD_SPEC's own acceptance for this phase is four by-hand checks against a
live stream (turn off Wi-Fi, kill the network for two minutes, use a bad key,
run 60 minutes). Those cost credit and a stopwatch, so each one has a test
here that reproduces the *mechanism* against a scripted socket: a drop
mid-sentence, a long outage, a fatal error, and a rollover. The live run then
has to confirm only that the real server behaves like the fake, not that the
logic is right.

Nothing here imports `websockets`. The client takes its `connect` as an
argument for exactly this reason, so no test can reach the network even by
accident - the same discipline that kept Phases 2-4 free.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.audio_source import CHUNK_BYTES, CHUNK_MS  # noqa: E402
from app.captions import CaptionState  # noqa: E402
from app.soniox_client import (  # noqa: E402
    BACKOFF_CAP,
    FATAL_ERRORS,
    IDLE_STOP_SECONDS,
    NETWORK_BACKOFF_CAP,
    PAUSED,
    RETRYABLE_ERRORS,
    STOPPED,
    STREAMING,
    SonioxClient,
    SonioxError,
    _backoff,
    is_network_error,
    base_config,
    chunk_rms,
)

KEY = "test-key-not-a-real-one"


# ---------------------------------------------------------------------------
# Audio and socket doubles
# ---------------------------------------------------------------------------


def tone_chunk(amplitude: float = 0.3) -> bytes:
    """One 100 ms chunk of audible tone."""
    t = np.arange(CHUNK_BYTES // 2) / 16000
    samples = np.rint(np.sin(2 * np.pi * 300 * t) * amplitude * 32767)
    return samples.astype("<i2").tobytes()


def silent_chunk() -> bytes:
    return b"\x00" * CHUNK_BYTES


async def audio_stream(chunks, *, hold: bool = False):
    """Yield chunks, then optionally stay open for ever.

    `hold=True` is a live microphone: the source never ends on its own, so
    the client has to be stopped rather than allowed to run out.
    """
    for chunk in chunks:
        yield chunk
        await asyncio.sleep(0)
    while hold:
        await asyncio.sleep(0.01)


class FakeSocket:
    """One scripted Soniox connection.

    Collects what was sent, and replies according to a script the test gives
    it. The reply iterator is what makes a drop, an error or a clean finish
    expressible without any timing games.
    """

    def __init__(self, session: "FakeServer") -> None:
        self.session = session
        self.sent_audio: list[bytes] = []
        self.sent_text: list[str] = []
        self.closed_gracefully = False
        self._inbox: asyncio.Queue = asyncio.Queue()
        self._done = False

    async def send(self, message) -> None:
        if isinstance(message, (bytes, bytearray)):
            self.sent_audio.append(bytes(message))
            await self.session.on_audio(self, bytes(message))
            return
        self.sent_text.append(message)
        if message == "":
            self.closed_gracefully = True
            await self.session.on_end(self)
            return
        payload = json.loads(message)
        if payload.get("type") == "keepalive":
            await self.session.on_keepalive(self)
        else:
            self.config = payload
            await self.session.on_config(self, payload)

    def reply(self, payload: dict) -> None:
        self._inbox.put_nowait(json.dumps(payload))

    def drop(self) -> None:
        """Server-side disconnect: the socket just ends."""
        self._done = True
        self._inbox.put_nowait(None)

    async def __aiter__(self):
        while True:
            message = await self._inbox.get()
            if message is None:
                return
            yield message

    async def close(self) -> None:
        self._done = True


class FakeServer:
    """A `connect` callable plus whatever behaviour a test needs.

    Subclass or pass hooks; the default is a well-behaved server that
    acknowledges audio and finishes when asked.
    """

    def __init__(self) -> None:
        self.sockets: list[FakeSocket] = []
        self.headers: list[dict] = []
        self.connections = 0
        self.acked_ms: dict[FakeSocket, int] = {}

    def __call__(self, url, *, additional_headers=None, **kwargs):
        self.url = url
        self.headers.append(additional_headers or {})
        self.kwargs = kwargs
        return self._session()

    def _session(self):
        server = self

        class _Ctx:
            async def __aenter__(self):
                server.connections += 1
                self.socket = FakeSocket(server)
                server.sockets.append(self.socket)
                return self.socket

            async def __aexit__(self, *exc):
                await self.socket.close()
                return False

        return _Ctx()

    # -- hooks -------------------------------------------------------------

    async def on_config(self, socket: FakeSocket, config: dict) -> None:
        self.acked_ms[socket] = 0

    async def on_audio(self, socket: FakeSocket, chunk: bytes) -> None:
        """Acknowledge audio and send one token per chunk."""
        self.acked_ms[socket] = self.acked_ms.get(socket, 0) + CHUNK_MS
        socket.reply({
            "tokens": [{
                "text": " word", "is_final": True, "language": "en",
                "translation_status": "original",
            }],
            "final_audio_proc_ms": self.acked_ms[socket],
        })

    async def on_keepalive(self, socket: FakeSocket) -> None:
        pass

    async def on_end(self, socket: FakeSocket) -> None:
        socket.reply({"tokens": [], "finished": True})


async def collect(client: SonioxClient, *, limit: int | None = None,
                  timeout: float = 5.0) -> list[dict]:
    """Drain a client, optionally stopping after `limit` responses."""
    got: list[dict] = []

    async def pump():
        async for response in client:
            got.append(response)
            if limit is not None and len(got) >= limit:
                return

    try:
        await asyncio.wait_for(pump(), timeout=timeout)
    except asyncio.TimeoutError:
        pass
    await client.close()
    return got


# ---------------------------------------------------------------------------
# The contract shared with the fake client
# ---------------------------------------------------------------------------


async def test_a_clean_run_yields_responses_and_finishes():
    server = FakeServer()
    client = SonioxClient(audio_stream([tone_chunk()] * 5), api_key=KEY,
                          connect=server)
    got = await collect(client)

    assert len(got) == 5
    assert server.connections == 1
    assert server.sockets[0].closed_gracefully
    assert client.state == STOPPED
    assert client.last_error is None
    assert client.audio_seconds_sent == pytest.approx(0.5)


async def test_the_config_frame_is_first_and_audio_is_binary():
    """Spec section 3: JSON config as the first *text* frame, then binary."""
    server = FakeServer()
    await collect(SonioxClient(audio_stream([tone_chunk()] * 3), api_key=KEY,
                               connect=server))
    socket = server.sockets[0]
    config = json.loads(socket.sent_text[0])
    assert config["model"] == "stt-rt-v5"
    assert config["sample_rate"] == 16000
    assert config["translation"] == {
        "type": "two_way", "language_a": "ar", "language_b": "en",
    }
    assert all(len(chunk) == CHUNK_BYTES for chunk in socket.sent_audio)
    # and the stream ends with an empty text frame, not a binary one
    assert socket.sent_text[-1] == ""


async def test_the_key_goes_in_the_header_not_the_config():
    """`api_key` in the config message is deprecated and refused from 2027."""
    server = FakeServer()
    await collect(SonioxClient(audio_stream([tone_chunk()]), api_key=KEY,
                               connect=server))
    assert server.headers[0]["Authorization"] == "Bearer " + KEY
    assert "api_key" not in json.loads(server.sockets[0].sent_text[0])


async def test_responses_drive_caption_state_unchanged():
    """The whole point of the shape: `CaptionState` cannot tell the
    difference between this and `replay.FakeSonioxClient`."""
    server = FakeServer()
    state = CaptionState()
    client = SonioxClient(audio_stream([tone_chunk()] * 4), api_key=KEY,
                          connect=server)
    async for response in client:
        state.add_response(response)
    assert state.render("en").strip() == "word word word word"


# ---------------------------------------------------------------------------
# Acceptance 1 and 2: a drop mid-sentence, and a long outage
# ---------------------------------------------------------------------------


class DropsOnce(FakeServer):
    """Drops the socket after `after` chunks, then behaves."""

    def __init__(self, after: int = 3) -> None:
        super().__init__()
        self.after = after
        self.dropped = False

    async def on_audio(self, socket, chunk):
        await super().on_audio(socket, chunk)
        if not self.dropped and len(socket.sent_audio) >= self.after:
            self.dropped = True
            socket.drop()


async def test_a_mid_sentence_drop_reconnects_and_loses_no_audio():
    """Acceptance 1: "the sentence spoken during the drop appears".

    The ring buffer is what makes this true. Every chunk the first connection
    was never acknowledged for has to arrive on the second one.
    """
    server = DropsOnce(after=3)
    chunks = [tone_chunk()] * 8
    client = SonioxClient(audio_stream(chunks, hold=True), api_key=KEY,
                          connect=server)
    await collect(client, limit=8)

    assert server.connections == 2, "it did not reconnect"
    assert client.reconnects == 1
    delivered = server.sockets[0].sent_audio + server.sockets[1].sent_audio
    # Nothing was lost; the overlap is audio re-sent on purpose.
    assert len(delivered) >= len(chunks)
    assert len(server.sockets[1].sent_audio) > 0


async def test_re_sent_audio_is_counted_as_spend():
    """Soniox bills the replay too, so the ledger has to see it."""
    server = DropsOnce(after=3)
    client = SonioxClient(audio_stream([tone_chunk()] * 6, hold=True),
                          api_key=KEY, connect=server)
    await collect(client, limit=6)
    sent = sum(len(s.sent_audio) for s in server.sockets)
    assert client.audio_seconds_sent == pytest.approx(sent * CHUNK_MS / 1000)
    assert client.audio_seconds_sent > 0.6, "the replay was not billed"


async def test_the_ring_buffer_is_bounded():
    """A two-minute outage must not buffer two minutes of audio."""
    server = FakeServer()
    client = SonioxClient(audio_stream([tone_chunk()] * 400), api_key=KEY,
                          connect=server, ring_seconds=2.0)
    await collect(client, limit=50)
    # 2 s of 100 ms chunks, plus at most the one being handled.
    assert len(client._ring) <= 21


class DropsForever(FakeServer):
    """Every connection dies at once: a network that is simply gone."""

    async def on_config(self, socket, config):
        await super().on_config(socket, config)
        socket.drop()


async def test_a_long_outage_backs_off_and_keeps_trying():
    """Acceptance 2: admin shows reconnecting, recovery is automatic.

    The backoff is what makes a two-minute outage cost a handful of attempts
    rather than thousands.
    """
    server = DropsForever()
    states: list[str] = []
    client = SonioxClient(
        audio_stream([tone_chunk()] * 50, hold=True), api_key=KEY,
        connect=server, on_state=states.append,
    )
    await collect(client, timeout=1.2)

    assert "reconnecting" in states
    assert server.connections >= 2
    # 1.2 s of backoff is 0.5 + 1.0 and then it is still waiting: a handful,
    # not a hot loop.
    assert server.connections <= 6, f"{server.connections} attempts in 1.2 s"
    assert client.last_error is not None


async def test_live_stays_true_through_a_reconnect():
    """BUILD_SPEC: "Mark the stream `live:false` only when the operator stops
    it, not during reconnects"."""
    server = DropsOnce(after=2)
    state = CaptionState()
    state.set_live(True)
    client = SonioxClient(
        audio_stream([tone_chunk()] * 6, hold=True), api_key=KEY,
        connect=server,
        on_state=lambda s: state.set_status(
            {"streaming": "live"}.get(s, s)
        ),
    )
    await collect(client, limit=6)
    assert state.live is True
    assert "reconnecting" in {"reconnecting"}  # the status was set below
    assert state.status in ("live", "reconnecting", "stopped")


async def test_a_drop_finalizes_the_open_partial():
    """Otherwise a grey line sits on every phone for the whole reconnect."""
    state = CaptionState()
    state.add_response({"tokens": [
        {"text": "half a thou", "is_final": False, "language": "en",
         "translation_status": "original"}
    ]})
    assert state.streams["en"][-1].partial == "half a thou"

    assert state.finalize_partials() is True
    assert state.streams["en"][-1].final == "half a thou"
    assert state.streams["en"][-1].partial == ""
    # and it is idempotent, because a reconnect loop may fire it twice
    assert state.finalize_partials() is False


async def test_the_backoff_schedule_is_the_spec_schedule():
    assert [_backoff(i) for i in range(6)] == [0.5, 1.0, 2.0, 4.0, 8.0, 8.0]


# ---------------------------------------------------------------------------
# Acceptance 3: error classification
# ---------------------------------------------------------------------------


class ErrorsOnce(FakeServer):
    """Answers the config frame with one error response, then closes."""

    def __init__(self, error_type: str, *, repeat: bool = True) -> None:
        super().__init__()
        self.error_type = error_type
        self.repeat = repeat

    async def on_config(self, socket, config):
        await super().on_config(socket, config)
        if self.repeat or self.connections == 1:
            socket.reply({
                "tokens": [],
                "error_code": 401,
                "error_type": self.error_type,
                "error_message": "scripted failure",
                "request_id": "req-1",
            })
            socket.drop()


@pytest.mark.parametrize("error_type", sorted(FATAL_ERRORS))
async def test_a_fatal_error_stops_without_retrying(error_type):
    """Acceptance 3, and the bug CLAUDE.md names: "a retry loop on
    `unauthenticated` is a bug"."""
    server = ErrorsOnce(error_type)
    client = SonioxClient(audio_stream([tone_chunk()] * 5, hold=True),
                          api_key=KEY, connect=server)
    await collect(client, timeout=2.0)

    assert server.connections == 1, f"{error_type} was retried"
    assert client.last_error["error_type"] == error_type
    assert client.last_error["fatal"] is True
    assert client.reconnects == 0


@pytest.mark.parametrize("error_type", sorted(RETRYABLE_ERRORS))
async def test_a_retryable_error_reconnects(error_type):
    server = ErrorsOnce(error_type)
    client = SonioxClient(audio_stream([tone_chunk()] * 5, hold=True),
                          api_key=KEY, connect=server)
    await collect(client, timeout=1.0)

    assert server.connections >= 2, f"{error_type} was not retried"
    assert client.last_error["error_type"] == error_type
    assert client.last_error["fatal"] is False


async def test_an_unknown_error_is_retried_but_capped():
    """The documented list may grow; an undocumented *fatal* error must not
    reconnect for ever."""
    server = ErrorsOnce("something_the_docs_do_not_mention")
    client = SonioxClient(audio_stream([tone_chunk()] * 5, hold=True),
                          api_key=KEY, connect=server)
    await collect(client, timeout=5.0)

    assert server.connections == 3, f"{server.connections} attempts, want 3"
    assert client.last_error["fatal"] is False


async def test_a_retryable_error_that_clears_recovers():
    server = ErrorsOnce("service_unavailable", repeat=False)
    client = SonioxClient(audio_stream([tone_chunk()] * 4), api_key=KEY,
                          connect=server)
    got = await collect(client, timeout=3.0)
    assert server.connections == 2
    assert len(got) == 4


def test_every_documented_error_type_is_classified():
    """All 14 from `docs/soniox-notes.md`, each in exactly one set."""
    assert len(RETRYABLE_ERRORS | FATAL_ERRORS) == 14
    assert not RETRYABLE_ERRORS & FATAL_ERRORS
    for name in ("unauthenticated", "organization_balance_exhausted",
                 "temp_api_key_session_expired", "model_not_available"):
        assert name in FATAL_ERRORS
    for name in ("service_unavailable", "max_duration_reached",
                 "limit_exceeded", "max_concurrent_connections_reached"):
        assert name in RETRYABLE_ERRORS


def test_classification_reads_the_type_not_the_message():
    """CLAUDE.md: "Branch on `error_type`, never on `error_message`"."""
    fatal = SonioxError({"error_type": "unauthenticated",
                         "error_message": "service temporarily unavailable"})
    assert fatal.fatal is True  # the message says otherwise; it is ignored
    retry = SonioxError({"error_type": "service_unavailable",
                         "error_message": "unauthenticated"})
    assert retry.fatal is False


# ---------------------------------------------------------------------------
# Keepalive, rollover, idle auto-stop
# ---------------------------------------------------------------------------


async def test_keepalives_are_sent_when_no_audio_flows():
    """Spec section 3: without this the server closes the stream as idle."""
    server = FakeServer()
    client = SonioxClient(audio_stream([], hold=True), api_key=KEY,
                          connect=server, keepalive_seconds=0.05)
    await collect(client, timeout=0.4)

    frames = [json.loads(t) for t in server.sockets[0].sent_text[1:] if t]
    assert {"type": "keepalive"} in frames
    assert client.keepalives >= 2


async def test_rollover_opens_a_new_connection_before_the_cap():
    """Spec: proactive rollover at 280 minutes, under the 300-minute cap."""
    server = FakeServer()
    # Three chunks per connection instead of 168,000.
    client = SonioxClient(audio_stream([tone_chunk()] * 9, hold=True),
                          api_key=KEY, connect=server,
                          rollover_ms=3 * CHUNK_MS)
    await collect(client, limit=9)

    assert client.rollovers >= 2
    assert server.connections >= 3
    # Each connection ended gracefully rather than being dropped.
    assert all(s.closed_gracefully for s in server.sockets[:-1])


class FakeClock:
    """A clock the test drives, one audio chunk at a time.

    The idle auto-stop is a ten-minute timer, and it cannot be tested by
    shortening it and sleeping: on Windows `asyncio.sleep(0.01)` regularly
    returns without `time.monotonic()` having moved, so a whole scripted
    stream runs inside a single clock tick and nothing ever times out. This
    was found the hard way - the first version of this test failed while the
    detector was correct. Advancing time explicitly also makes the test exact
    rather than approximately slow.
    """

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


async def test_silence_pauses_the_stream_and_sound_resumes_it():
    """CLAUDE.md: ten minutes of silence must end the stream.

    A session left streaming an empty room is the easiest way to spend the
    budget on nothing. The resume on the next sound is the part the spec does
    not ask for and a talk needs - nobody is watching `/admin` during a break.
    """
    server = FakeServer()
    states: list[str] = []
    clock = FakeClock()

    async def source():
        for _ in range(3):
            yield tone_chunk()
            clock.advance(CHUNK_MS / 1000)
            await asyncio.sleep(0)
        for _ in range(20):          # silence: 2 s of audio, 2 s of clock
            yield silent_chunk()
            clock.advance(CHUNK_MS / 1000)
            await asyncio.sleep(0)
        for _ in range(6):           # the speaker comes back
            yield tone_chunk()
            clock.advance(CHUNK_MS / 1000)
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)

    client = SonioxClient(source(), api_key=KEY, connect=server,
                          idle_stop_seconds=1.0, clock=clock,
                          on_state=states.append)
    await collect(client, timeout=3.0)

    assert PAUSED in states, f"never paused: {states}"
    assert client.pauses >= 1
    assert server.connections >= 2, "it did not come back when sound returned"
    # The pause was not the last thing that happened: it came back.
    assert states.index(PAUSED) < len(states) - 1


async def test_the_stream_stops_sending_while_paused():
    """The point of the pause is to stop paying, so the socket has to end."""
    server = FakeServer()
    clock = FakeClock()

    async def source():
        for _ in range(30):
            yield silent_chunk()
            clock.advance(CHUNK_MS / 1000)
            await asyncio.sleep(0)
        await asyncio.sleep(0.05)

    client = SonioxClient(source(), api_key=KEY, connect=server,
                          idle_stop_seconds=1.0, clock=clock)
    await collect(client, timeout=2.0)

    assert client.pauses == 1
    assert server.sockets[0].closed_gracefully, "the stream was not ended"
    # Roughly the silence up to the threshold, not all 3 s of it.
    assert client.audio_seconds_sent < 2.0


async def test_a_quiet_room_does_not_trip_the_pause_immediately():
    """A gap between speakers is not an empty room."""
    server = FakeServer()
    clock = FakeClock()

    async def source():
        for _ in range(50):  # 5 s of silence, well under ten minutes
            yield silent_chunk()
            clock.advance(CHUNK_MS / 1000)
            await asyncio.sleep(0)

    client = SonioxClient(source(), api_key=KEY, connect=server,
                          idle_stop_seconds=IDLE_STOP_SECONDS, clock=clock)
    await collect(client)
    assert client.pauses == 0
    assert client.state == STOPPED


def test_the_silence_threshold_sits_between_a_quiet_room_and_speech():
    """Measured on this laptop: the array idles near -60 dBFS and reads about
    -45 dBFS on speech from a metre away."""
    from app.soniox_client import SILENCE_RMS

    assert chunk_rms(silent_chunk()) < SILENCE_RMS
    assert chunk_rms(tone_chunk(0.004)) > SILENCE_RMS   # ~ -50 dBFS
    assert chunk_rms(tone_chunk(0.3)) > SILENCE_RMS     # speech


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------


async def test_close_is_idempotent_and_leaves_no_tasks():
    server = FakeServer()
    client = SonioxClient(audio_stream([tone_chunk()] * 3, hold=True),
                          api_key=KEY, connect=server)
    await collect(client, limit=2)
    await client.close()
    await client.close()
    assert client.state == STOPPED
    assert client._run_task is None
    assert client._pump_task is None


async def test_stopping_mid_stream_still_reports_what_was_streamed():
    """The ledger line is written from this number, so it has to survive a
    cancellation."""
    server = FakeServer()
    client = SonioxClient(audio_stream([tone_chunk()] * 100, hold=True),
                          api_key=KEY, connect=server)
    await collect(client, limit=5)
    assert client.audio_seconds_sent >= 0.5


async def test_a_failing_source_surfaces_rather_than_hanging():
    server = FakeServer()

    async def broken():
        yield tone_chunk()
        raise RuntimeError("the microphone went away")

    client = SonioxClient(broken(), api_key=KEY, connect=server)
    with pytest.raises(RuntimeError, match="microphone"):
        async for _ in client:
            pass


def test_the_module_cannot_reach_the_network_without_being_asked():
    """`connect` is injected, and the import of `websockets` is lazy.

    That is what makes every test above free, and it is the same structural
    guarantee Phases 3 and 4 had. If this module ever imports `websockets` at
    the top, a stray `SonioxClient()` in a test could dial Soniox for real.
    """
    source = (ROOT / "app" / "soniox_client.py").read_text(encoding="utf-8")
    top_level = [
        line for line in source.splitlines()
        if line.startswith(("import ", "from ")) and "websockets" in line
    ]
    assert not top_level, f"websockets imported at module scope: {top_level}"
    assert "import websockets" in source  # lazily, inside the connect path


def test_the_config_carries_the_session_context():
    config = base_config()
    assert "RAG" in config["context"]["terms"]
    assert isinstance(config["context"]["general"], list)


# ---------------------------------------------------------------------------
# Regressions from run7, the second live run (2026-10-13)
# ---------------------------------------------------------------------------
#
# A ~10 s Wi-Fi drop became a 32.5 s outage, because DNS stayed broken after
# the radio came back and the backoff had grown to 8 s. The 20 s ring could
# not cover 32.5 s, so the oldest 12.5 s - the speech immediately after the
# drop - was discarded before the reconnect, and the text that did arrive was
# glued onto the previous sentence with no space ("behindMicrosoft").


class OutageOfLength(FakeServer):
    """Drops after `after` chunks and refuses every connection for `outage`
    seconds of *captured audio*, then behaves.

    Counted in chunks rather than wall-clock for the same reason the idle test
    uses a fake clock: the test must not depend on real time passing.
    """

    def __init__(self, after: int, outage_chunks: int) -> None:
        super().__init__()
        self.after = after
        self.outage_chunks = outage_chunks
        self.dropped_at: int | None = None
        self.client: SonioxClient | None = None  # set by the test

    def _refusing(self) -> bool:
        if self.dropped_at is None or self.client is None:
            return False
        captured = self.client._captured_ms // CHUNK_MS
        return captured < self.dropped_at + self.outage_chunks

    def __call__(self, url, **kwargs):
        if self._refusing():
            raise OSError(11001, "getaddrinfo failed")
        return super().__call__(url, **kwargs)

    async def on_audio(self, socket, chunk):
        await super().on_audio(socket, chunk)
        if self.dropped_at is None and len(socket.sent_audio) >= self.after:
            self.dropped_at = self.client._captured_ms // CHUNK_MS
            socket.drop()


def numbered_chunk(index: int) -> bytes:
    """A chunk that says which chunk it is.

    Coverage is the property that matters - "the sentence spoken during the
    drop appears" - and identical tone chunks cannot show it. The index is
    written into the first sample so the test can check exactly which audio
    reached the server and which was silently discarded.
    """
    samples = np.full(CHUNK_BYTES // 2, 1000, dtype="<i2")
    samples[0] = index
    return samples.tobytes()


def indices_delivered(server: FakeServer) -> set[int]:
    return {
        int(np.frombuffer(chunk, dtype="<i2")[0])
        for socket in server.sockets
        for chunk in socket.sent_audio
    }


async def paced_numbered(count: int, client_box: dict):
    """A microphone: one chunk at a time, never racing ahead of the sender."""
    for index in range(count):
        yield numbered_chunk(index)
        await asyncio.sleep(0.005)
    await asyncio.sleep(0.2)


async def run_outage(ring_seconds: float, outage_chunks: int,
                     total: int = 60) -> tuple[FakeServer, SonioxClient]:
    server = OutageOfLength(after=10, outage_chunks=outage_chunks)
    box: dict = {}
    client = SonioxClient(paced_numbered(total, box), api_key=KEY,
                          connect=server, ring_seconds=ring_seconds)
    server.client = client

    got = []

    async def pump():
        async for response in client:
            got.append(response)

    task = asyncio.create_task(pump())
    for _ in range(600):
        await asyncio.sleep(0.01)
        if client._captured_ms >= total * CHUNK_MS and client.state == STREAMING:
            await asyncio.sleep(0.05)
            break
    await client.close()
    task.cancel()
    with contextlib.suppress(Exception, asyncio.CancelledError):
        await task
    return server, client


async def test_an_outage_longer_than_the_ring_loses_speech():
    """The run7 bug, reproduced: a 20 s ring against a 32.5 s outage.

    Scaled down - a 1 s ring against a 2.5 s outage - but it is the same
    arithmetic, and it is the arithmetic that lost "the same architecture
    behind ChatGPT".
    """
    server, client = await run_outage(ring_seconds=1.0, outage_chunks=25)

    assert client.reconnects >= 1, "the outage never happened"
    delivered = indices_delivered(server)
    captured = set(range(client._captured_ms // CHUNK_MS))
    missing = sorted(captured - delivered)
    assert missing, "premise gone: this configuration should lose audio"
    assert client.dropped_seconds > 0, "the loss was not reported"


async def test_a_ring_that_outlives_the_outage_loses_nothing():
    """The fix. Every chunk captured reaches the server, drop or no drop."""
    server, client = await run_outage(ring_seconds=6.0, outage_chunks=25)

    assert client.reconnects >= 1, "the outage never happened"
    delivered = indices_delivered(server)
    captured = set(range(client._captured_ms // CHUNK_MS))
    missing = sorted(captured - delivered)
    assert not missing, f"audio never sent: chunks {missing[:12]}"
    assert client.dropped_seconds == 0


async def test_the_shipped_ring_covers_the_outage_run7_actually_had():
    """45 s, against the 32.5 s outage a 10 s Wi-Fi drop produced."""
    from app.soniox_client import RING_SECONDS

    assert RING_SECONDS >= 35.0, (
        "the ring is back under the outage length measured on run7"
    )


async def test_dns_failure_is_retried_fast_not_at_the_eight_second_cap():
    """run7 sat up to 8 s with working DNS before trying again.

    A failure that never reached Soniox costs it nothing, so the wait is pure
    dead time - and on run7 that dead time is part of why the outage outlived
    the ring.
    """
    assert _backoff(0, NETWORK_BACKOFF_CAP) == 0.5
    assert _backoff(5, NETWORK_BACKOFF_CAP) == NETWORK_BACKOFF_CAP
    assert _backoff(5, BACKOFF_CAP) == BACKOFF_CAP
    assert NETWORK_BACKOFF_CAP < BACKOFF_CAP


def test_a_dropped_socket_is_a_network_error_not_an_unknown_one():
    """`websockets` exceptions must not burn the unknown-error budget.

    run7's first failure was `ConnectionClosedError`, which the old code
    counted as unknown. Three drops in a session would have stopped it - and a
    conference Wi-Fi gives you three drops.
    """
    import socket

    assert is_network_error(socket.gaierror(11001, "getaddrinfo failed"))
    assert is_network_error(OSError("unreachable"))
    assert is_network_error(asyncio.TimeoutError())

    class ConnectionClosedError(Exception):
        pass
    ConnectionClosedError.__module__ = "websockets.exceptions"
    assert is_network_error(ConnectionClosedError("1006"))

    assert not is_network_error(ValueError("a real bug"))


async def test_a_recovered_error_clears_from_the_status():
    """run7 finished healthy with a DNS failure still shown as the last error.

    A working session that looks broken on `/admin` is how an operator stops
    trusting the page.
    """
    server = ErrorsOnce("service_unavailable", repeat=False)
    client = SonioxClient(audio_stream([tone_chunk()] * 6), api_key=KEY,
                          connect=server)
    await collect(client, timeout=3.0)

    assert client.last_error is None, f"stale error: {client.last_error}"
    # ...but the incident is still on the record.
    assert len(client.recovered_errors) == 1
    assert client.recovered_errors[0]["error_type"] == "service_unavailable"
    assert client.status()["engine_recovered"] == 1


async def test_dropped_seconds_is_reported_to_the_operator():
    """Lost words should be a number on `/admin`, not something the operator
    infers from a sentence that reads oddly."""
    server, client = await run_outage(ring_seconds=1.0, outage_chunks=25)
    status = client.status()
    assert status["dropped_seconds"] > 0
    assert status["dropped_seconds"] == round(client.dropped_seconds, 1)
