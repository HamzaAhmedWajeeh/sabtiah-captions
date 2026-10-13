"""Viewer WebSocket hub: one `CaptionState`, many phones.

The hub never parses Soniox and never builds caption text. It decides only
*who* gets *which* snapshot or diff, and *when*; assembly stays in
`captions.py`, which is pure. So this file is about fan-out and backpressure.

Three requirements from BUILD_SPEC section 7, Phase 3, shape it:

- **"Throttle broadcasts to at most 10 per second; coalesce diffs in
  between."** State changes far faster than that - run1 moves `CaptionState`
  1,247 times in 300 s - so the loop sleeps `1/hz` after every flush and lets
  `diff_since` collapse everything that happened during the sleep into one
  message.
- **"Drop the slowest if its send buffer backs up."** Every viewer owns a
  writer task and a one-message slot. A phone still writing simply misses a
  flush; because its `last_seq` has not advanced, the next flush hands it a
  *wider* diff rather than a backlog of narrow ones. Only a viewer that
  accepts nothing for `stall_timeout` is closed, and its own reconnect brings
  it back with a fresh snapshot.
- **"Read-only: ignore any client message except `{"type":"resync"}`."** A
  resync does not write from the receive path - two concurrent `send_text`
  calls on one socket interleave frames - it raises a flag the next flush
  honours. Every byte a viewer receives comes from its own writer task.

`flush()` does no socket I/O: it only fills slots. That keeps the broadcast
loop immune to one slow phone, and makes the hub testable with a fake socket
and no event-loop tricks (`tests/test_broadcast.py`).
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Callable, Protocol

from app.captions import CaptionState

#: BUILD_SPEC: "Limit to 500 concurrent viewer sockets."
MAX_VIEWERS = 500

#: BUILD_SPEC: "at most 10 per second".
BROADCAST_HZ = 10

#: Heartbeat on the viewer socket. An application-level `{"type":"ping"}`
#: rather than a WebSocket control frame: Starlette exposes no API for
#: protocol pings, and an application ping is the more useful signal anyway -
#: it proves the caption path is alive, not just the socket, and it stops the
#: Cloudflare tunnel reaping an idle connection between talks.
#:
#: **BUILD_SPEC says 20 s; this is 5.** On run9 (2026-10-13) the laptop's
#: Wi-Fi was switched off and the phone's socket died without either end
#: sending a FIN, so the phone sat in `OPEN` believing it was connected and
#: showed the same frozen line for the rest of the talk. The client now
#: reconnects when it has heard nothing for 12 s, and that watchdog is only
#: as good as the heartbeat feeding it: at 20 s the page would have to sit
#: dead for most of a minute before noticing. Five seconds costs each phone
#: about 20 bytes every five seconds.
PING_INTERVAL = 5.0

#: How long a viewer may fail to accept anything before it is dropped.
STALL_TIMEOUT = 5.0

#: 1013 is "try again later", which is what a rejected or dropped phone
#: should do - and its reconnect backoff already does.
CLOSE_TRY_LATER = 1013


class ViewerSocket(Protocol):
    """The slice of Starlette's WebSocket the hub actually uses."""

    async def send_text(self, text: str) -> None: ...

    async def close(self, code: int = 1000) -> None: ...


def _dump(payload: dict) -> str:
    """Compact JSON with Arabic left as Arabic.

    `ensure_ascii=False` is not cosmetic: escaping Arabic to a `\\uXXXX`
    sequence per character roughly triples the bytes of every Arabic caption,
    and the audience is on phone data in a basement.
    """
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


PING = _dump({"type": "ping"})

#: Passed to `Viewer.offer` for messages that must not advance a viewer's
#: sequence number - a ping tells it nothing about caption state.
NO_ADVANCE = 0


class Viewer:
    """One connected page: a socket, a writer task, and a one-message slot.

    `last_seq` is what this viewer has actually been *sent*. It advances in
    the writer after a successful send, never at offer time, so a diff
    computed from it can never skip text that failed to go out.
    """

    def __init__(self, socket: ViewerSocket, *, ident: int) -> None:
        self.socket = socket
        self.ident = ident
        self.last_seq = 0
        self.closed = False
        self.want_snapshot = False
        self.stalled_since: float | None = None
        self.sent = 0
        self._pending: tuple[int, str] | None = None
        self._busy = False
        self._wake = asyncio.Event()

    @property
    def busy(self) -> bool:
        """True while a message is queued or in flight on the socket."""
        return self._busy

    def offer(self, advance_to: int, text: str) -> bool:
        """Fill the slot. False if the previous message has not gone out yet."""
        if self.closed or self._busy:
            return False
        self._pending = (advance_to, text)
        self._busy = True
        self._wake.set()
        return True

    async def run_writer(self) -> None:
        """Drain the slot onto the socket until the viewer is closed."""
        while not self.closed:
            await self._wake.wait()
            self._wake.clear()
            item, self._pending = self._pending, None
            if item is None:
                self._busy = False
                continue
            advance_to, text = item
            try:
                await self.socket.send_text(text)
            except Exception:
                # The phone vanished mid-write. Locking the slot and exiting
                # is enough: the receive side raises too, and the endpoint
                # removes the viewer.
                self.closed = True
                return
            self.last_seq = max(self.last_seq, advance_to)
            self.sent += 1
            self._busy = False

    def shutdown(self) -> None:
        """Make `run_writer` return at its next opportunity."""
        self.closed = True
        self._pending = None
        self._wake.set()


class BroadcastHub:
    """Fan-out from one `CaptionState` to every connected page."""

    def __init__(
        self,
        state: CaptionState,
        *,
        max_viewers: int = MAX_VIEWERS,
        hz: float = BROADCAST_HZ,
        ping_interval: float = PING_INTERVAL,
        stall_timeout: float = STALL_TIMEOUT,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.state = state
        self.max_viewers = max_viewers
        self.interval = 1.0 / hz
        self.ping_interval = ping_interval
        self.stall_timeout = stall_timeout
        self.clock = clock
        self.viewers: list[Viewer] = []
        self.rejected = 0
        self.dropped = 0
        self._changed = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._writers: dict[int, asyncio.Task] = {}
        self._next_ident = 1
        self._next_ping = clock() + ping_interval

    # -- viewers -----------------------------------------------------------

    @property
    def viewer_count(self) -> int:
        return len(self.viewers)

    async def add(self, socket: ViewerSocket) -> Viewer | None:
        """Accept a page - full snapshot first, then diffs. None if refused.

        The snapshot is written inline rather than through the slot: the
        writer task does not exist yet, so there is no second writer to
        interleave with, and a page that waited up to 100 ms for its first
        paint would feel broken.
        """
        if len(self.viewers) >= self.max_viewers:
            self.rejected += 1
            await _quietly(socket.close(CLOSE_TRY_LATER))
            return None

        viewer = Viewer(socket, ident=self._next_ident)
        self._next_ident += 1
        snapshot = self.state.snapshot()
        try:
            await socket.send_text(_dump(snapshot))
        except Exception:
            viewer.closed = True
            await _quietly(socket.close(CLOSE_TRY_LATER))
            return None
        # From the snapshot, not from `state.seq`: the state may have moved
        # on during the await above, and this viewer has not seen that yet.
        viewer.last_seq = snapshot["seq"]
        viewer.sent = 1

        self.viewers.append(viewer)
        self._writers[viewer.ident] = asyncio.create_task(
            viewer.run_writer(), name=f"viewer-{viewer.ident}"
        )
        return viewer

    async def remove(self, viewer: Viewer) -> None:
        """Detach a viewer. Safe to call twice, and never blocks on its socket."""
        if viewer in self.viewers:
            self.viewers.remove(viewer)
        viewer.shutdown()
        task = self._writers.pop(viewer.ident, None)
        if task is not None:
            # Cancelled, not awaited to completion: a writer stuck in
            # `send_text` to a dead phone would otherwise stall the broadcast
            # loop here, which is the failure this whole file exists to avoid.
            await _settle(task)

    def rebind(self, state: CaptionState) -> None:
        """Point the hub at a fresh `CaptionState` and resync every viewer.

        Starting a second session builds a new state whose `seq` restarts at
        0. Without forcing a snapshot, every connected page would sit at a
        `last_seq` from the old session, decide it is already ahead, and show
        the previous talk forever.
        """
        self.state = state
        for viewer in self.viewers:
            viewer.last_seq = 0
            viewer.want_snapshot = True
        self.notify()

    def resync(self, viewer: Viewer) -> None:
        """Honour `{"type":"resync"}`: a full snapshot on the next flush.

        A flag rather than a send, so the socket keeps exactly one writer.
        Repeat requests collapse into one snapshot, so a client stuck in a
        resync loop costs the server one message per flush at most.
        """
        viewer.want_snapshot = True
        self.notify()

    # -- broadcasting ------------------------------------------------------

    def notify(self) -> None:
        """Called when `CaptionState` changed. Sync and cheap on purpose."""
        self._changed.set()

    async def flush(self) -> None:
        """Hand every viewer whatever it is missing. No awaits on sockets.

        Diffs are computed per distinct `last_seq`, not per viewer: in the
        steady state every viewer sits at the same sequence number, so 500
        phones cost one `diff_since` and one `json.dumps` per flush.
        """
        now = self.clock()
        ping_due = now >= self._next_ping
        if ping_due:
            self._next_ping = now + self.ping_interval

        seq = self.state.seq
        diffs: dict[int, str] = {}
        drop: list[Viewer] = []

        for viewer in self.viewers:
            if viewer.closed:
                drop.append(viewer)
                continue

            if viewer.want_snapshot:
                text, advance_to, is_ping = _dump(self.state.snapshot()), seq, False
            elif viewer.last_seq < seq:
                base = viewer.last_seq
                if base not in diffs:
                    diffs[base] = _dump(self.state.diff_since(base))
                text, advance_to, is_ping = diffs[base], seq, False
            elif ping_due:
                text, advance_to, is_ping = PING, NO_ADVANCE, True
            else:
                viewer.stalled_since = None
                continue

            if viewer.offer(advance_to, text):
                viewer.want_snapshot = False
                viewer.stalled_since = None
            elif is_ping:
                # A skipped ping is no evidence of a stall: the viewer is busy
                # receiving captions, which is better proof of life.
                continue
            elif viewer.stalled_since is None:
                viewer.stalled_since = now
            elif now - viewer.stalled_since >= self.stall_timeout:
                drop.append(viewer)

        for viewer in drop:
            self.dropped += 1
            await self.remove(viewer)
            await _quietly(viewer.socket.close(CLOSE_TRY_LATER))

    async def run(self) -> None:
        """Wake on a change or a due ping, flush, then throttle."""
        while True:
            timeout = max(self._next_ping - self.clock(), 0.0)
            try:
                await asyncio.wait_for(self._changed.wait(), timeout or 0.001)
            except (asyncio.TimeoutError, TimeoutError):
                pass
            self._changed.clear()
            await self.flush()
            # The throttle, and the only reason diffs coalesce: everything
            # that changes during this sleep lands in one message.
            await asyncio.sleep(self.interval)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run(), name="broadcast")

    async def stop(self) -> None:
        """Stop the loop and close every viewer. Safe to call twice."""
        if self._task is not None:
            await _settle(self._task)
            self._task = None
        for viewer in list(self.viewers):
            await self.remove(viewer)
            await _quietly(viewer.socket.close(CLOSE_TRY_LATER))

    def status(self) -> dict[str, Any]:
        """Counters for `/admin`. No caption text, no secrets."""
        return {
            "viewers": self.viewer_count,
            "max_viewers": self.max_viewers,
            "rejected": self.rejected,
            "dropped": self.dropped,
            "seq": self.state.seq,
            "live": self.state.live,
        }


async def _quietly(awaitable) -> None:
    """Await something whose failure is not worth hearing about.

    Closing an already-dead socket is the normal case here, not an incident.
    """
    try:
        await awaitable
    except Exception:
        pass


async def _settle(task: asyncio.Task) -> None:
    """Cancel a task and wait for it, inheriting neither its result nor error.

    `asyncio.wait` is used rather than `await task` so the task's exception
    cannot propagate into the caller and its cancellation cannot be mistaken
    for the caller's own.
    """
    task.cancel()
    await asyncio.wait({task})
    if not task.cancelled():
        task.exception()
