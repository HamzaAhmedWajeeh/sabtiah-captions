"""Locks the decoder-shutdown contract in tools/probe_soniox.py.

Phase 1 hit a real hang here on Windows: terminating ffmpeg while its stdout
pipe was still full left `proc.wait()` blocked forever, so the probe appeared
to freeze right after printing the error it was trying to report. The fix is
to drain stdout before reaping. These tests encode that ordering, plus the
timeouts that keep a wedged decoder from outliving the probe.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import probe_soniox as P  # noqa: E402


class FakeReader:
    """An asyncio.StreamReader stand-in holding a fixed payload."""

    def __init__(self, payload: bytes = b"", *, never_eof: bool = False):
        self._payload = payload
        self._never_eof = never_eof

    async def read(self, size: int = -1) -> bytes:
        if self._payload:
            block, self._payload = self._payload[:size], self._payload[size:]
            return block
        if self._never_eof:  # a pipe whose writer never closes
            await asyncio.sleep(3600)
        return b""


class FakeProc:
    """A decoder that, like the real one, cannot be reaped until drained."""

    def __init__(self, stdout=b"", stderr=b"", *, wait_needs_drain=True,
                 unreapable=False):
        self.stdout = FakeReader(stdout)
        self.stderr = FakeReader(stderr)
        self.returncode = None
        self.terminated = False
        self.killed = False
        self._wait_needs_drain = wait_needs_drain
        self._unreapable = unreapable

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        if self._unreapable:
            await asyncio.sleep(3600)
        if self._wait_needs_drain:
            while self.stdout._payload:
                await asyncio.sleep(0)
        self.returncode = 1
        return self.returncode


@pytest.mark.asyncio
async def test_drains_stdout_so_the_process_can_be_reaped():
    proc = FakeProc(stdout=b"x" * 200_000, stderr=b"")
    stderr = await P.stop_ffmpeg(proc)
    assert proc.terminated
    assert proc.returncode == 1  # wait() completed
    assert stderr == ""


@pytest.mark.asyncio
async def test_returns_stderr_text_stripped():
    proc = FakeProc(stdout=b"", stderr=b"  Invalid data found\n")
    assert await P.stop_ffmpeg(proc) == "Invalid data found"


@pytest.mark.asyncio
async def test_already_exited_process_is_not_terminated_again():
    proc = FakeProc()
    proc.returncode = 0
    await P.stop_ffmpeg(proc)
    assert not proc.terminated


@pytest.fixture
def impatient(monkeypatch):
    """Shorten stop_ffmpeg's own 10 s guards so the tests stay fast.

    Only the 10 s timeouts are rewritten, so a test still fails if the
    guards are removed outright.
    """
    real = asyncio.wait_for

    async def fast(aw, timeout):
        return await real(aw, 0.05 if timeout == 10 else timeout)

    monkeypatch.setattr(asyncio, "wait_for", fast)


@pytest.mark.asyncio
async def test_a_pipe_that_never_closes_does_not_hang_the_probe(impatient):
    proc = FakeProc(unreapable=True)
    proc.stdout = FakeReader(b"", never_eof=True)
    assert await asyncio.wait_for(P.stop_ffmpeg(proc), timeout=5) == ""
    assert proc.terminated


@pytest.mark.asyncio
async def test_unreapable_decoder_is_killed(impatient):
    proc = FakeProc(unreapable=True)
    await asyncio.wait_for(P.stop_ffmpeg(proc), timeout=5)
    assert proc.killed


@pytest.mark.asyncio
async def test_drain_tolerates_a_broken_pipe():
    class Broken:
        async def read(self, size=-1):
            raise BrokenPipeError

    assert await P.drain(Broken()) == b""
