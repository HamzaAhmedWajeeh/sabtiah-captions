"""The real Soniox connection: audio in, response dicts out.

BUILD_SPEC Phase 5, and the spec's own words about it: "This is what makes or
breaks the live demo." Everything here exists because a conference Wi-Fi drops
packets, a laptop sleeps its radio, and a talk runs longer than a stream is
allowed to.

The shape is deliberately the same as `replay.FakeSonioxClient`: an async
iterator of Soniox response dicts. `CaptionState` and the broadcast hub cannot
tell which one they are attached to, which is what let Phases 2-4 be built and
tested with no network at all - and what lets this module be tested the same
way, by injecting a fake `connect`.

What it guarantees, and where each guarantee comes from:

- **Audio is not lost to a short drop.** A ring buffer keeps the last 20 s;
  on reconnect, everything Soniox never acknowledged is re-sent before live
  audio resumes (spec: "so a short drop loses nothing").
- **A drop does not look like the end of the talk.** `live` stays true and the
  status goes `reconnecting`; only the operator sets `live` false.
- **A retry loop on a fatal error is a bug.** Errors are classified by
  `error_type`, never by message (CLAUDE.md). All 14 documented types are
  listed below; an unrecognised one is retried, but only three times, so an
  undocumented fatal error cannot loop for ever.
- **Silence is not billed.** Ten minutes below the silence threshold ends the
  stream and shows `paused`; the first sound after that reconnects. $10 has to
  cover the project, and a session left open over a coffee break is the
  easiest way to spend it on nothing.
- **Every second sent is counted.** `audio_seconds_sent` includes re-sent ring
  audio, because Soniox bills that too.

The one place this does not follow the spec's letter is rollover; see
`_ROLLOVER_NOTE` below.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import time
from collections import deque
from collections.abc import AsyncIterator
from typing import Any, Callable

import numpy as np

from app.audio_source import CHUNK_MS, SAMPLE_RATE
from app.settings import settings, soniox_context

WS_URL = "wss://stt-rt.soniox.com/transcribe-websocket"

#: Connection states, as BUILD_SPEC Phase 5 names them, plus `paused` for the
#: idle auto-stop CLAUDE.md requires.
IDLE = "idle"
CONNECTING = "connecting"
STREAMING = "streaming"
RECONNECTING = "reconnecting"
PAUSED = "paused"
STOPPED = "stopped"

#: Transient: back off and reconnect. `max_duration_reached` is here because
#: the documented remedy for the 300-minute cap is simply a new connection.
RETRYABLE_ERRORS = frozenset({
    "service_unavailable",
    "internal_error",
    "request_timeout",
    "max_duration_reached",
    "limit_exceeded",
    "max_concurrent_connections_reached",
})

#: Permanent: stop, show it, do not loop. A retry loop on `unauthenticated`
#: is the specific bug CLAUDE.md calls out.
FATAL_ERRORS = frozenset({
    "unauthenticated",
    "permission_denied",
    "organization_balance_exhausted",
    "organization_monthly_budget_exhausted",
    "project_monthly_budget_exhausted",
    "invalid_request",
    "model_not_available",
    "temp_api_key_session_expired",
})

#: All 14 documented types, verified against the API reference on 2026-10-10
#: (`docs/soniox-notes.md`). Asserted at import so a future edit that drops or
#: double-classifies a type fails loudly rather than silently retrying
#: something fatal.
assert not RETRYABLE_ERRORS & FATAL_ERRORS
assert len(RETRYABLE_ERRORS | FATAL_ERRORS) == 14

#: Backoff schedule from the spec: 0.5s, 1s, 2s, 4s, capped at 8s.
BACKOFF_BASE = 0.5
BACKOFF_CAP = 8.0

#: A lower cap for failures that never reached Soniox at all - DNS, a refused
#: socket, a dead route. Those cost the server nothing, so there is no reason
#: to be polite about them, and the wait is pure dead time: on run7 the
#: attempt that finally succeeded was scheduled 8 s after the one before it,
#: so the laptop may have had working DNS for most of those 8 s and simply
#: sat there. Errors Soniox *reported* keep the 8 s cap, because hammering a
#: service that answered "unavailable" is rude and does not help.
NETWORK_BACKOFF_CAP = 2.0

#: An unrecognised `error_type` is treated as retryable - the documented list
#: may grow - but capped, because an undocumented *fatal* error would
#: otherwise reconnect for ever.
MAX_UNKNOWN_ATTEMPTS = 3

#: Spec section 3 allows 40 s between keepalives; the spec's own pipeline uses
#: 20 s and so does this, because the cost is one tiny frame and the failure
#: is a dropped connection mid-talk.
KEEPALIVE_SECONDS = 20.0

#: Audio kept for replay after a drop. It only has to outlive
#: `MAX_REPLAY_SECONDS` below, so BUILD_SPEC's 20 s is enough again - the 45 s
#: of the previous commit was sized to *lose nothing*, which is no longer the
#: goal.
RING_SECONDS = 20.0

#: **The most that is ever re-sent after a reconnect**, and the decision that
#: shapes this whole module's behaviour on a bad network (Hamza, 2026-10-13):
#: *prefer staying live*.
#:
#: Soniox acknowledges at roughly real time, so replaying N seconds leaves the
#: captions N seconds late for the rest of the session - run7 replayed 21.7 s
#: and then ran ~20 s behind for its remaining 110 seconds. For an audience
#: reading a live talk that is worse than missing the words: late captions are
#: wrong captions, attached to whatever the speaker is saying now.
#:
#: So a short drop still loses nothing, a long one loses its oldest audio, and
#: `CaptionState.add_notice` tells the room it happened.
MAX_REPLAY_SECONDS = 10.0

#: Proactive rollover, under the documented 300-minute cap.
ROLLOVER_MS = 280 * 60 * 1000

#: Idle auto-stop (CLAUDE.md): ten minutes below the threshold ends the
#: stream. -54 dBFS is below a quiet room on this laptop's array (which idles
#: around -60 dBFS and reads about -45 dBFS on speech from a metre away) and
#: far below any speech, so a pause in the talk does not trip it but an empty
#: room does.
IDLE_STOP_SECONDS = 600.0
SILENCE_RMS = 0.002

#: How long a connection must carry responses before it counts as healthy and
#: the backoff resets. Without this, a connection that dies immediately after
#: its first response would keep restarting the schedule at 0.5 s for ever.
HEALTHY_AFTER_RESPONSES = 1

_ROLLOVER_NOTE = """
BUILD_SPEC says: "open a new connection, start sending, then close the old one
gracefully". This module instead closes gracefully *first* and opens the new
connection immediately after, replaying the ring buffer to cover the gap.

The spec's overlap avoids losing audio across the switch. So does the ring
replay, which already has to exist for reconnects - and the overlap costs a
second connection's worth of billing on every rollover plus two live sockets
to reason about, for a path that only runs after 280 minutes of streaming.
$10 does not buy 280 minutes, so on this project the overlap would be
untestable complexity guarding an event that cannot happen.
"""


class SonioxError(Exception):
    """An error response from Soniox, classified by `error_type`."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.error_type = str(payload.get("error_type") or "unknown")
        self.error_code = payload.get("error_code")
        self.request_id = payload.get("request_id")
        # Kept for the operator to read, never branched on (CLAUDE.md).
        self.error_message = str(payload.get("error_message") or "")
        super().__init__(f"{self.error_type}: {self.error_message}")

    @property
    def fatal(self) -> bool:
        return self.error_type in FATAL_ERRORS

    @property
    def known(self) -> bool:
        return self.error_type in RETRYABLE_ERRORS or self.fatal

    def as_dict(self) -> dict[str, Any]:
        return {
            "error_type": self.error_type,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "request_id": self.request_id,
            "fatal": self.fatal,
        }


def chunk_rms(chunk: bytes) -> float:
    """RMS of one audio chunk, 0..1 against int16 full scale."""
    if not chunk:
        return 0.0
    samples = np.frombuffer(chunk, dtype="<i2").astype(np.float32) / 32768.0
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(samples))))


def base_config(context: dict | None = None) -> dict:
    """The config frame from BUILD_SPEC section 3, with our context."""
    return {
        "model": "stt-rt-v5",
        "audio_format": "pcm_s16le",
        "sample_rate": SAMPLE_RATE,
        "num_channels": 1,
        "language_hints": ["ar", "en"],
        "enable_language_identification": True,
        "enable_speaker_diarization": False,
        "enable_endpoint_detection": True,
        "context": context if context is not None else soniox_context(),
        "translation": {
            "type": "two_way", "language_a": "ar", "language_b": "en",
        },
    }


class SonioxClient:
    """A resilient Soniox stream. Async-iterate it for response dicts.

    `audio` is anything that yields 100 ms chunks of 16 kHz mono int16 - in
    practice an `audio_source.AudioSource`, in tests a plain async generator.

    `connect` is injected so the whole module can be tested offline. It is
    called as `connect(url, additional_headers=..., max_size=None,
    ping_interval=20)` and must return an async context manager yielding
    something with `send`, `close` and `__aiter__` - the shape
    `websockets.connect` already has.
    """

    def __init__(
        self,
        audio: AsyncIterator[bytes],
        *,
        api_key: str | None = None,
        context: dict | None = None,
        config: dict | None = None,
        url: str = WS_URL,
        connect: Callable[..., Any] | None = None,
        on_state: Callable[[str], None] | None = None,
        on_drop: Callable[[], None] | None = None,
        keepalive_seconds: float = KEEPALIVE_SECONDS,
        ring_seconds: float = RING_SECONDS,
        rollover_ms: int = ROLLOVER_MS,
        idle_stop_seconds: float = IDLE_STOP_SECONDS,
        silence_rms: float = SILENCE_RMS,
        max_unknown_attempts: int = MAX_UNKNOWN_ATTEMPTS,
        max_replay_seconds: float = MAX_REPLAY_SECONDS,
        on_audio_dropped: Callable[[float], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._audio = audio
        self._api_key = api_key if api_key is not None else settings.soniox_api_key
        self._config = config if config is not None else base_config(context)
        self._url = url
        self._connect = connect
        self._on_state = on_state
        self._on_drop = on_drop
        self.keepalive_seconds = keepalive_seconds
        self.ring_seconds = ring_seconds
        self.rollover_ms = rollover_ms
        self.idle_stop_seconds = idle_stop_seconds
        self.silence_rms = silence_rms
        self.max_unknown_attempts = max_unknown_attempts
        self.max_replay_seconds = max_replay_seconds
        self._on_audio_dropped = on_audio_dropped
        #: Injected so the idle auto-stop can be tested without waiting ten
        #: minutes. It cannot be tested by scaling the timeout down either:
        #: on Windows `asyncio.sleep(0.01)` routinely returns without
        #: `time.monotonic()` having advanced at all - a whole scripted
        #: stream can run inside one clock tick - so a test that shortens
        #: `idle_stop_seconds` and sleeps measures nothing. Same reasoning as
        #: the injected `connect`.
        self._now = clock

        self.state = IDLE
        self.last_error: dict[str, Any] | None = None
        #: Errors that were survived. Kept so a recovered incident is still
        #: visible after the fact without `last_error` claiming the session is
        #: currently broken.
        self.recovered_errors: list[dict[str, Any]] = []
        self.responses = 0
        self.reconnects = 0
        self.rollovers = 0
        self.reconfigures = 0
        self.keepalives = 0
        self.pauses = 0
        #: Audio actually put on the wire, re-sent ring audio included. This
        #: is the number the budget cares about.
        self.audio_seconds_sent = 0.0
        #: Audio that was captured, never acknowledged, and fell out of the
        #: ring before a connection came back. Words the audience never saw.
        self.dropped_seconds = 0.0

        self._captured_ms = 0
        self._conn_start_ms = 0
        self._conn_sent_ms = 0
        self._acked_ms = 0
        self._global_acked_ms = 0
        self._dropped_to = 0
        #: Dropped audio not yet announced. Held until a connection is proved
        #: working, so a failed attempt does not put a notice on the page for
        #: a reconnect that never happened - and a storm of attempts produces
        #: one notice, not seven.
        self._pending_drop = 0.0
        #: Set by `reconfigure()`. The sender ends the stream gracefully at
        #: the next chunk and the run loop opens a new one with the new
        #: config - the rollover path, for a different reason.
        self._reconfigure = False
        self._conn_responses = 0

        self._ring: deque[tuple[int, bytes]] = deque()
        self._send_queue: asyncio.Queue[bytes | None] | None = None
        self._events: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
        self._audio_ended = False
        self._closing = False
        self._paused = False
        self._pause_requested = False
        self._silent_since: float | None = None
        self._sound = asyncio.Event()
        self._pump_task: asyncio.Task | None = None
        self._run_task: asyncio.Task | None = None

    # -- observable state --------------------------------------------------

    def _set_state(self, state: str) -> None:
        if state == self.state:
            return
        self.state = state
        if self._on_state is not None:
            self._on_state(state)

    def reconfigure(self, config: dict) -> bool:
        """Swap the config and reconnect to apply it, losing no audio.

        Soniox takes its `context` in the config frame, so a change to the
        speaker list only reaches it on a new connection. That is the same
        problem rollover already solves: end the current stream gracefully,
        open another, and let the ring buffer cover the join. The audience
        sees nothing - a planned reconnect is not a drop, so there is no gap
        notice and `live` never changes.

        Returns True if a reconnect was scheduled, False if nothing is
        running and the new config will simply be used at the next Start.
        """
        self._config = config
        if self.state not in (STREAMING, CONNECTING, RECONNECTING):
            return False
        self._reconfigure = True
        return True

    @property
    def lag_seconds(self) -> float:
        """Audio sent but not yet acknowledged, in seconds.

        BUILD_SPEC's admin page wants "estimated delay (wall clock now minus
        the wall-clock time the audio at `final_audio_proc_ms` was sent)".
        With audio sent at real time those are the same quantity, and this one
        needs no clock.
        """
        return max(0, self._conn_sent_ms - self._acked_ms) / 1000

    @property
    def silent_seconds(self) -> float:
        if self._silent_since is None:
            return 0.0
        return self._now() - self._silent_since

    def status(self) -> dict[str, Any]:
        """For `/admin`. No key, no caption text."""
        return {
            "engine_state": self.state,
            "engine_error": self.last_error,
            "engine_recovered": len(self.recovered_errors),
            "engine_last_recovered": (
                self.recovered_errors[-1] if self.recovered_errors else None
            ),
            "dropped_seconds": round(self.dropped_seconds, 1),
            "engine_responses": self.responses,
            "reconnects": self.reconnects,
            "rollovers": self.rollovers,
            "reconfigures": self.reconfigures,
            "keepalives": self.keepalives,
            "pauses": self.pauses,
            "streamed_seconds": round(self.audio_seconds_sent, 1),
            "lag_seconds": round(self.lag_seconds, 2),
            "silent_seconds": round(self.silent_seconds, 1),
        }

    # -- the audio pump ----------------------------------------------------

    async def _pump(self) -> None:
        """Read the source for the life of the client.

        One pump across every connection, not one per connection: the source
        is a microphone and cannot be rewound, so a reconnect must not
        interrupt reading it. Audio captured while there is no connection goes
        into the ring and is sent when one comes back.
        """
        try:
            async for chunk in self._audio:
                self._captured_ms += CHUNK_MS
                self._ring.append((self._captured_ms, chunk))
                self._trim_ring()
                self._note_level(chunk)
                queue = self._send_queue
                if queue is not None:
                    queue.put_nowait(chunk)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # the source failed, not the connection
            self._events.put_nowait(("source_error", exc))
        finally:
            self._audio_ended = True
            queue = self._send_queue
            if queue is not None:
                queue.put_nowait(None)

    def _trim_ring(self) -> None:
        horizon = self._captured_ms - int(self.ring_seconds * 1000)
        while self._ring and self._ring[0][0] <= horizon:
            self._ring.popleft()

    def _note_level(self, chunk: bytes) -> None:
        """Track silence for the idle auto-stop, and sound for the resume."""
        if chunk_rms(chunk) >= self.silence_rms:
            self._silent_since = None
            self._sound.set()
            return
        if self._silent_since is None:
            self._silent_since = self._now()
        elif (not self._pause_requested and not self._paused
              and self.silent_seconds >= self.idle_stop_seconds):
            self._pause_requested = True

    # -- send queue --------------------------------------------------------

    def _open_send_queue(self) -> asyncio.Queue[bytes | None]:
        """A fresh queue for a new connection, pre-filled from the ring.

        Built without awaiting, so the pump cannot interleave a live chunk
        into the middle of the replay.
        """
        # The replay window, not the ring, is what bounds how far back we go:
        # the ring is just storage, this is policy.
        floor = self._captured_ms - int(self.max_replay_seconds * 1000)
        resume_from = max(self._global_acked_ms, floor)
        if floor > self._global_acked_ms:
            # The outage outlived the ring. Whatever Soniox never acknowledged
            # and the ring no longer holds is simply gone, and the operator
            # should be able to see that rather than infer it from a sentence
            # that reads oddly.
            lost = (floor - max(self._global_acked_ms, self._dropped_to)) / 1000
            if lost > 0:
                self.dropped_seconds += lost
                self._pending_drop += lost
                self._dropped_to = floor
        queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        replayed = [c for end_ms, c in self._ring if end_ms > resume_from]
        first_end = next(
            (end_ms for end_ms, _ in self._ring if end_ms > resume_from), None
        )
        # The connection's own audio clock starts at the beginning of the
        # first chunk it will be sent, which is what makes
        # `final_audio_proc_ms` convertible back to a global offset.
        self._conn_start_ms = (
            first_end - CHUNK_MS if first_end is not None else self._captured_ms
        )
        for chunk in replayed:
            queue.put_nowait(chunk)
        if self._audio_ended:
            queue.put_nowait(None)
        self._send_queue = queue
        self._conn_sent_ms = 0
        self._acked_ms = 0
        self._conn_responses = 0
        return queue

    # -- one connection ----------------------------------------------------

    async def _one_connection(self) -> str:
        """Run a single connection. Returns why it ended, or raises.

        Reasons: `finished` (the audio ran out and Soniox acknowledged the
        end), `rollover`, `paused`. Anything else is an exception.
        """
        connect = self._connect
        if connect is None:  # imported lazily: tests never need websockets
            import websockets

            connect = websockets.connect
        queue = self._open_send_queue()

        async with connect(
            self._url,
            additional_headers={"Authorization": "Bearer " + self._api_key},
            max_size=None,
            ping_interval=20,
        ) as ws:
            await ws.send(json.dumps(self._config))
            self._set_state(STREAMING)
            sender = asyncio.create_task(self._sender(ws, queue), name="sx-send")
            receiver = asyncio.create_task(self._receiver(ws), name="sx-recv")
            try:
                await asyncio.wait(
                    {sender, receiver}, return_when=asyncio.FIRST_COMPLETED
                )
                # **The sender is asked first, and the order matters.** When
                # the sender ends the stream on purpose - a rollover, or the
                # idle pause - it sends the empty text frame, Soniox answers
                # `{"finished": true}` and the receiver completes too. Reading
                # the receiver first in that case reports "finished", and the
                # run loop treats a rollover as the end of the talk: the
                # session simply stops after 280 minutes instead of carrying
                # on, and the idle pause never resumes. Both were silent until
                # the rollover and pause tests were written.
                if sender.done() and not sender.cancelled():
                    reason = sender.result()  # re-raises a send failure
                    # Soniox now flushes its remaining tokens; awaiting the
                    # receiver also re-raises an error response it may have
                    # read, which must win over the reason above.
                    try:
                        await asyncio.wait_for(receiver, timeout=30)
                    except asyncio.TimeoutError:
                        receiver.cancel()
                        await _quietly(receiver)
                    return reason

                # The server ended the stream by itself - an error, almost
                # always. Stop sending immediately: every chunk after this is
                # billed and discarded.
                sender.cancel()
                await _quietly(sender)
                return receiver.result()  # raises SonioxError if it failed
            finally:
                for task in (sender, receiver):
                    if not task.done():
                        task.cancel()
                        await _quietly(task)
                self._send_queue = None
                self._global_acked_ms = self._conn_start_ms + self._acked_ms

    async def _sender(self, ws: Any, queue: asyncio.Queue) -> str:
        """Audio out as binary frames; keepalives when nothing is flowing."""
        while True:
            try:
                chunk = await asyncio.wait_for(
                    queue.get(), timeout=self.keepalive_seconds
                )
            except asyncio.TimeoutError:
                # Spec section 3: without this the server closes the
                # connection as idle (WebSocket 1001).
                await ws.send(json.dumps({"type": "keepalive"}))
                self.keepalives += 1
                continue

            if chunk is None:
                await ws.send("")  # empty *text* frame: the graceful end
                return "finished"

            await ws.send(chunk)
            self._conn_sent_ms += CHUNK_MS
            self.audio_seconds_sent += CHUNK_MS / 1000

            if self._pause_requested:
                await ws.send("")
                return "paused"
            if self._reconfigure:
                self._reconfigure = False
                await ws.send("")
                return "reconfigure"
            if self._conn_sent_ms >= self.rollover_ms:
                await ws.send("")
                return "rollover"

    async def _receiver(self, ws: Any) -> str:
        """Responses in. Raises `SonioxError` on an error response."""
        async for message in ws:
            if isinstance(message, (bytes, bytearray)):
                continue  # Soniox speaks JSON text; nothing else is expected
            try:
                data = json.loads(message)
            except ValueError:
                continue
            if not isinstance(data, dict):
                continue
            if data.get("error_type"):
                raise SonioxError(data)
            acked = data.get("final_audio_proc_ms")
            if isinstance(acked, int):
                self._acked_ms = max(self._acked_ms, acked)
            # Only responses that actually carry tokens are forwarded. A
            # bare `{"tokens": [], "finished": true}` is the end-of-stream
            # acknowledgement, not caption data; `CaptionState` would no-op
            # it, but counting it would make `responses` disagree with what
            # reached the page.
            if data.get("tokens"):
                self.responses += 1
                self._conn_responses += 1
                if self._conn_responses == HEALTHY_AFTER_RESPONSES:
                    if self.last_error is not None:
                        # Recovered. Leaving the old error on `/admin` makes a
                        # working session look broken - run7 finished healthy
                        # with a DNS failure still showing as the last error.
                        self.recovered_errors.append(self.last_error)
                        self.last_error = None
                    if self._pending_drop > 0:
                        # Announced here rather than when the audio was
                        # discarded: this is the first moment the page is
                        # certainly live again, so the notice lands in front
                        # of the text that follows it.
                        dropped, self._pending_drop = self._pending_drop, 0.0
                        if self._on_audio_dropped is not None:
                            self._on_audio_dropped(dropped)
                self._events.put_nowait(("response", data))
            if data.get("finished"):
                return "finished"
        # The socket closed without `finished`: a drop, not a clean end.
        raise ConnectionError("connection closed before the stream finished")

    # -- the reconnect loop ------------------------------------------------

    async def _run(self) -> None:
        attempt = 0
        unknown_attempts = 0
        try:
            while not self._closing:
                if self._paused:
                    await self._wait_for_sound()
                    continue

                backoff_cap = BACKOFF_CAP
                self._set_state(RECONNECTING if attempt else CONNECTING)
                try:
                    reason = await self._one_connection()
                except asyncio.CancelledError:
                    raise
                except SonioxError as exc:
                    self.last_error = exc.as_dict()
                    if exc.fatal:
                        break
                    if not exc.known:
                        unknown_attempts += 1
                        if unknown_attempts >= self.max_unknown_attempts:
                            break
                except Exception as exc:
                    network = is_network_error(exc)
                    self.last_error = {
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                        "fatal": False,
                        "network": network,
                    }
                    if not network:
                        # Something unrecognised that was not a dropped link.
                        # Retried under the same cap as an unknown
                        # `error_type`, so it cannot loop for ever.
                        unknown_attempts += 1
                        if unknown_attempts >= self.max_unknown_attempts:
                            break
                    backoff_cap = (NETWORK_BACKOFF_CAP if network
                                   else BACKOFF_CAP)
                else:
                    if reason == "finished":
                        break
                    if reason in ("rollover", "reconfigure"):
                        # Planned, not a failure: no backoff, no reconnect
                        # count, no `reconnecting` state, and nothing the
                        # audience is told about. The boundary is still
                        # marked, because the next stream's first token
                        # carries no leading space of its own.
                        if reason == "rollover":
                            self.rollovers += 1
                        else:
                            self.reconfigures += 1
                        if self._on_drop is not None:
                            self._on_drop()
                        attempt = 0
                        continue
                    if reason == "paused":
                        self._enter_pause()
                        continue

                if self._conn_responses >= HEALTHY_AFTER_RESPONSES:
                    # The connection worked before it broke, so this is a new
                    # incident, not a failing retry chain.
                    attempt = 0
                    unknown_attempts = 0
                if self._closing:
                    break
                self.reconnects += 1
                self._set_state(RECONNECTING)
                if self._on_drop is not None:
                    self._on_drop()
                await asyncio.sleep(_backoff(attempt, backoff_cap))
                attempt += 1
        except asyncio.CancelledError:
            raise
        finally:
            self._set_state(STOPPED if self._closing else self.state)
            self._events.put_nowait(("done", None))

    def _enter_pause(self) -> None:
        """Idle auto-stop: the stream is closed, the session is not."""
        self._paused = True
        self._pause_requested = False
        self.pauses += 1
        self._sound.clear()
        self._set_state(PAUSED)

    async def _wait_for_sound(self) -> None:
        """Hold in `paused` until the room makes a noise again.

        CLAUDE.md asks for the auto-stop but not for a resume. Waiting for
        sound rather than for the operator is the behaviour a talk actually
        needs: nobody is watching `/admin` during the break, and a session
        that silently stays dead until someone notices is worse than one that
        costs a few seconds of credit when the speaker starts again.
        """
        await self._sound.wait()
        if self._closing:
            return
        self._paused = False
        self._silent_since = None

    # -- public interface --------------------------------------------------

    async def __aiter__(self) -> AsyncIterator[dict]:
        self._closing = False
        self._pump_task = asyncio.create_task(self._pump(), name="sx-pump")
        self._run_task = asyncio.create_task(self._run(), name="sx-run")
        try:
            while True:
                kind, payload = await self._events.get()
                if kind == "response":
                    yield payload
                elif kind == "source_error":
                    raise payload
                elif kind == "done":
                    return
        finally:
            await self.close()

    async def close(self) -> None:
        """Stop everything. Safe to call twice, and from a `finally`."""
        self._closing = True
        self._sound.set()  # release a paused wait
        for task in (self._run_task, self._pump_task):
            if task is not None and not task.done():
                task.cancel()
                await _quietly(task)
        self._run_task = None
        self._pump_task = None
        self._set_state(STOPPED)


def _backoff(attempt: int, cap: float = BACKOFF_CAP) -> float:
    """0.5s, 1s, 2s, 4s, 8s, 8s, ... (BUILD_SPEC Phase 5), under `cap`."""
    return min(BACKOFF_BASE * math.pow(2, attempt), cap)


def is_network_error(exc: BaseException) -> bool:
    """True for a failure that never reached Soniox.

    `socket.gaierror` (DNS) and the rest of `OSError` are the obvious half.
    The other half is whatever `websockets` raises when a live connection
    dies - `ConnectionClosedError` and friends - which is a dropped network
    link, not a refusal. Matched by module name so this file does not have to
    import `websockets`, which is what keeps every test offline.
    """
    if isinstance(exc, (OSError, ConnectionError, asyncio.TimeoutError)):
        return True
    return type(exc).__module__.split(".")[0] == "websockets"


async def _quietly(task: asyncio.Task) -> None:
    """Await a task whose failure is not worth hearing about."""
    with contextlib.suppress(Exception, asyncio.CancelledError):
        await task
