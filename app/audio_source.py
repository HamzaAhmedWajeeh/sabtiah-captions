"""Audio capture: the microphone, and a file that imitates one.

BUILD_SPEC section 6, Phase 4. Everything downstream of here wants one thing
and one thing only: **16 kHz mono int16 in exact 100 ms chunks** (3200 bytes),
which is what Soniox `stt-rt-v5` is fed. Both sources deliver precisely that,
so `soniox_client.py` in Phase 5 never learns where its audio came from.

Two implementations behind one interface:

- `MicSource` - a live `sounddevice.RawInputStream`. The device picks the
  format it can actually do; this module converts. A laptop array microphone
  typically offers 44.1/48 kHz and several channels and refuses 16 kHz mono
  outright, so capturing at the native rate and converting here is the normal
  path, not a fallback.
- `FileSource` - a decoded recording, optionally paced at real time. This is
  what lets the whole capture path be tested with no microphone, no mixer and
  no network, which is the same trick `replay.FakeSonioxClient` plays one
  layer up.

Nothing here touches the network, so **no part of this module can spend
Soniox credit** (CLAUDE.md's budget rule). Capturing audio is free; only
Phase 5's client makes it cost anything.

Both sources carry a live `Level` for the operator meter, and count the audio
they have produced so the usage log can record it.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import shutil
import subprocess
import sys
import time
import wave
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from app.settings import RECORDING_DIR

#: What Soniox is fed, and therefore what every source must emit.
SAMPLE_RATE = 16000
CHUNK_MS = 100
CHUNK_SAMPLES = SAMPLE_RATE * CHUNK_MS // 1000  # 1600
CHUNK_BYTES = CHUNK_SAMPLES * 2  # mono int16

#: int16 full scale, used for every level figure below.
FULL_SCALE = 32768.0

#: A sample this close to full scale has probably been clipped by the
#: interface rather than merely being loud. The meter must show this: a
#: clipped line-out is the one input problem that cannot be fixed later, and
#: at the venue it is the mixer's gain knob, not anything in this code.
CLIP_THRESHOLD = 0.995

#: Quietest level the meter bothers to distinguish, in dBFS.
FLOOR_DB = -60.0

#: Live audio buffered between the capture callback and the consumer. Ten
#: seconds is far more than the pipeline ever needs; it exists so that a
#: momentary stall (a slow first Soniox frame, a garbage collection) does not
#: lose speech. Past that, the *oldest* audio is dropped rather than letting
#: the queue grow without bound - in a live talk, audio that is already ten
#: seconds late has no value, and unbounded growth is how a long session dies.
QUEUE_CHUNKS = 100


def _db(amplitude: float) -> float:
    """Amplitude in 0..1 as dBFS, floored so silence is a number not -inf."""
    if amplitude <= 0:
        return FLOOR_DB
    return max(FLOOR_DB, 20 * math.log10(amplitude))


@dataclass
class Level:
    """One chunk's loudness, for the admin meter.

    `rms` drives the bar, `peak` the clip indicator. Both are 0..1 against
    int16 full scale. `clipping` latches for a short while in `AudioSource`
    so a brief overload is still visible on a page that polls once a second.
    """

    rms: float = 0.0
    peak: float = 0.0
    clipping: bool = False

    @classmethod
    def of(cls, samples: np.ndarray) -> "Level":
        if samples.size == 0:
            return cls()
        scaled = samples.astype(np.float32) / FULL_SCALE
        rms = float(np.sqrt(np.mean(np.square(scaled))))
        peak = float(np.max(np.abs(scaled)))
        return cls(rms=rms, peak=peak, clipping=peak >= CLIP_THRESHOLD)

    def as_dict(self) -> dict[str, Any]:
        return {
            "rms": round(self.rms, 4),
            "peak": round(self.peak, 4),
            "db": round(_db(self.rms), 1),
            "peak_db": round(_db(self.peak), 1),
            "clipping": self.clipping,
        }


class _Resampler:
    """Linear resampling with enough state to survive chunk boundaries.

    BUILD_SPEC allows "polyphase or simple linear ... for speech". Linear it
    is, with two details that matter:

    1. **It carries state.** Resampling each 100 ms block independently puts a
       discontinuity at every boundary - ten clicks a second, which a speech
       model hears as consonants. The fractional read position and the tail
       samples needed for the next interpolation are kept between calls.
    2. **It low-passes before decimating.** 48 kHz to 16 kHz throws away two
       of every three samples, and anything above 8 kHz in the input folds
       back down into the speech band as aliasing. A box filter the length of
       the decimation ratio is a crude anti-alias filter, but it is far better
       than none and costs a single convolution.
    """

    def __init__(self, src_rate: int, dst_rate: int = SAMPLE_RATE) -> None:
        self.src_rate = src_rate
        self.dst_rate = dst_rate
        self.step = src_rate / dst_rate
        #: Box length for the anti-alias filter; 1 means "no filtering",
        #: which is right when upsampling or passing through.
        self.box = max(1, int(round(self.step))) if src_rate > dst_rate else 1
        self._history = np.zeros(self.box - 1, dtype=np.float32)
        self._tail = np.zeros(0, dtype=np.float32)
        self._pos = 0.0

    def push(self, samples: np.ndarray) -> np.ndarray:
        """Convert one block of source samples. Returns int16 at `dst_rate`."""
        if samples.size == 0:
            return np.zeros(0, dtype=np.int16)
        if self.src_rate == self.dst_rate:
            return samples.astype(np.int16)

        block = samples.astype(np.float32)

        if self.box > 1:
            padded = np.concatenate([self._history, block])
            self._history = padded[-(self.box - 1):].copy()
            kernel = np.full(self.box, 1.0 / self.box, dtype=np.float32)
            # 'valid' over (box-1 history + n new) yields exactly n samples,
            # each filtered with the samples that really preceded it.
            block = np.convolve(padded, kernel, mode="valid")

        buffer = np.concatenate([self._tail, block])
        if buffer.size < 2:
            self._tail = buffer
            return np.zeros(0, dtype=np.int16)

        # How many output samples the buffer can support without
        # extrapolating past its last sample.
        span = (buffer.size - 1) - self._pos
        count = int(math.floor(span / self.step)) + 1 if span >= 0 else 0
        if count <= 0:
            self._tail = buffer
            return np.zeros(0, dtype=np.int16)

        index = self._pos + self.step * np.arange(count, dtype=np.float64)
        out = np.interp(index, np.arange(buffer.size), buffer)

        next_pos = self._pos + self.step * count
        drop = min(int(math.floor(next_pos)), buffer.size)
        self._tail = buffer[drop:]
        self._pos = next_pos - drop

        return np.clip(np.rint(out), -32768, 32767).astype(np.int16)


class _Recorder:
    """Writes captured audio to a WAV file. Off unless asked for.

    Recording is opt-in because the room has not consented to being recorded
    (CLAUDE.md: "Logging and recording are both off by default - privacy
    consent matters"). The file lands in `recordings/`, which is gitignored
    and not reachable through any route.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._wav = wave.open(str(path), "wb")
        self._wav.setnchannels(1)
        self._wav.setsampwidth(2)
        self._wav.setframerate(SAMPLE_RATE)

    def write(self, chunk: bytes) -> None:
        self._wav.writeframes(chunk)

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._wav.close()

    @staticmethod
    def default_path() -> Path:
        """`recordings/<date>-<time>.wav`.

        BUILD_SPEC says `<date>`; the time is included so a second run on the
        same day cannot silently overwrite the first one's audio.
        """
        stamp = time.strftime("%Y-%m-%d-%H%M%S")
        return RECORDING_DIR / f"{stamp}.wav"


class AudioSource:
    """Common behaviour of every source: chunking, levels, counters.

    Subclasses produce int16 samples at whatever rate they have and call
    `_emit`; this class converts, slices into exact 100 ms chunks, measures
    them, records them if asked, and hands them on.
    """

    #: What `/admin` calls this source.
    kind = "none"

    def __init__(self, *, record: bool | Path = False) -> None:
        self.sample_rate = SAMPLE_RATE
        self.level = Level()
        self.chunks_out = 0
        self.dropped_chunks = 0
        self.started_at: float | None = None
        self._pending = bytearray()
        self._clip_until = 0.0
        self._recorder: _Recorder | None = None
        self._record_request = record

    # -- lifecycle ---------------------------------------------------------

    async def open(self) -> None:
        """Acquire whatever the source needs, before anything iterates it.

        Separate from `__aiter__` so a device that cannot be opened fails
        where the operator asked for it - as a 400 on `/admin/start` naming
        the device - rather than inside a background task, where the only
        trace would be a line in the status table. Opening the recorder here
        too means `/admin` can show the file's path as soon as Start returns,
        rather than once the first chunk happens to arrive.
        """
        self._open_recorder()

    @property
    def audio_ms(self) -> int:
        """Audio this source has produced. Not yet audio anyone was billed
        for - that is the Soniox client's count in Phase 5."""
        return self.chunks_out * CHUNK_MS

    @property
    def audio_seconds(self) -> float:
        return self.audio_ms / 1000

    @property
    def recording_path(self) -> str | None:
        return str(self._recorder.path) if self._recorder else None

    def describe(self) -> str:
        return self.kind

    def status(self) -> dict[str, Any]:
        """The source half of `/admin/status`."""
        return {
            "source_kind": self.kind,
            "source_detail": self.describe(),
            "level": self.level.as_dict(),
            "audio_ms": self.audio_ms,
            "dropped_chunks": self.dropped_chunks,
            "recording": self.recording_path,
        }

    async def close(self) -> None:
        if self._recorder is not None:
            self._recorder.close()
            self._recorder = None

    # -- the chunk pipeline ------------------------------------------------

    def _open_recorder(self) -> None:
        request = self._record_request
        if not request:
            return
        path = request if isinstance(request, Path) else _Recorder.default_path()
        self._recorder = _Recorder(path)

    def _emit(self, samples: np.ndarray) -> list[bytes]:
        """Measure and slice converted samples into whole 100 ms chunks.

        Returns zero or more chunks; a block that is not a multiple of 100 ms
        leaves its remainder in `_pending` for the next call, because a short
        frame sent to Soniox is a frame of silence inserted into speech.
        """
        if samples.size:
            self._observe(samples)
            self._pending.extend(samples.astype("<i2").tobytes())

        out: list[bytes] = []
        while len(self._pending) >= CHUNK_BYTES:
            chunk = bytes(self._pending[:CHUNK_BYTES])
            del self._pending[:CHUNK_BYTES]
            self.chunks_out += 1
            if self._recorder is not None:
                self._recorder.write(chunk)
            out.append(chunk)
        return out

    def _observe(self, samples: np.ndarray) -> None:
        """Update the meter. Clipping latches for 1.5 s so a single overloaded
        chunk is still lit when a page that polls at 1 Hz next looks."""
        level = Level.of(samples)
        now = time.monotonic()
        if level.clipping:
            self._clip_until = now + 1.5
        level.clipping = now < self._clip_until
        self.level = level


class MicSource(AudioSource):
    """Live capture from an input device.

    The `sounddevice` callback runs on PortAudio's own thread and must not
    touch the event loop directly; it hands chunks over with
    `loop.call_soon_threadsafe(queue.put_nowait, ...)` (CLAUDE.md). It also
    must not block, so conversion is a couple of numpy operations on 100 ms of
    audio and nothing else - no I/O, no allocation of consequence, no logging.
    """

    kind = "mic"

    def __init__(
        self,
        device: int | str | None = None,
        *,
        channel: int = 1,
        record: bool | Path = False,
    ) -> None:
        super().__init__(record=record)
        self.device = device
        self.channel = max(1, channel)
        self.device_name = ""
        self.device_rate = SAMPLE_RATE
        self.device_channels = 1
        self.native = True
        self.callback_errors = 0
        self._queue: asyncio.Queue[bytes] | None = None
        self._stream: Any = None
        self._resampler: _Resampler | None = None

    def describe(self) -> str:
        detail = f"{self.device_name or self.device} @ {self.device_rate} Hz"
        if not self.native:
            detail += f" -> {SAMPLE_RATE} Hz"
        if self.device_channels > 1:
            detail += f", ch {self.channel}/{self.device_channels}"
        return detail

    def status(self) -> dict[str, Any]:
        return {
            **super().status(),
            "device": self.device,
            "device_name": self.device_name,
            "device_rate": self.device_rate,
            "resampling": not self.native,
            "callback_errors": self.callback_errors,
        }

    # -- device negotiation ------------------------------------------------

    def _choose_format(self) -> None:
        """Ask for 16 kHz; settle for the rate the device admits to.

        Deliberately pessimistic: `check_input_settings` is cheap and failing
        here produces a readable message, whereas opening a stream with
        settings the driver rejects raises PortAudio's own error from inside
        the callback thread, where it is much harder to explain.

        The *rate* is negotiated; the *channel count* is not. A multi-channel
        device is always opened with every channel and the channel picked in
        the callback, because asking a multi-channel interface for mono makes
        the driver downmix - which on the venue's USB box would sum the live
        mic into whatever else is on the other inputs, and would throw away
        the operator's channel setting without saying so anywhere. Taking
        16 kHz mono from a stereo interface was exactly that bug.
        """
        import sounddevice as sd

        info = sd.query_devices(self.device, "input")
        self.device_name = str(info.get("name", "")).strip()
        max_channels = int(info.get("max_input_channels", 1)) or 1
        if self.channel > max_channels:
            raise ValueError(
                f"device has {max_channels} input channel(s), "
                f"--channel {self.channel} does not exist"
            )
        self.device_channels = max_channels

        try:
            sd.check_input_settings(
                device=self.device, channels=self.device_channels,
                samplerate=SAMPLE_RATE, dtype="int16",
            )
            self.device_rate = SAMPLE_RATE
            self.native = True
            return
        except Exception:
            pass

        self.device_rate = int(round(float(info.get("default_samplerate")
                                           or SAMPLE_RATE)))
        # `native` is about the rate alone - a 16 kHz stereo device needs no
        # resampling, only channel selection.
        self.native = self.device_rate == SAMPLE_RATE
        sd.check_input_settings(
            device=self.device, channels=self.device_channels,
            samplerate=self.device_rate, dtype="int16",
        )

    # -- capture -----------------------------------------------------------

    async def open(self) -> None:
        import sounddevice as sd

        self._choose_format()
        self._resampler = _Resampler(self.device_rate)
        self._queue = asyncio.Queue(maxsize=QUEUE_CHUNKS)
        loop = asyncio.get_running_loop()
        channels = self.device_channels
        pick = self.channel - 1

        def callback(indata, frames, time_info, status) -> None:
            if status:
                self.callback_errors += 1
            samples = np.frombuffer(bytes(indata), dtype="<i2")
            if channels > 1:
                samples = samples.reshape(-1, channels)[:, pick]
            converted = self._resampler.push(samples)
            for chunk in self._emit(converted):
                try:
                    loop.call_soon_threadsafe(self._offer, chunk)
                except RuntimeError:
                    # The loop closed while PortAudio was still inside this
                    # callback. Normal at shutdown and not worth reporting:
                    # raising here only turns a clean exit into a stack trace
                    # on the venue laptop's console, printed from a thread
                    # nobody is watching.
                    return

        self._stream = sd.RawInputStream(
            device=self.device,
            samplerate=self.device_rate,
            channels=channels,
            dtype="int16",
            blocksize=int(self.device_rate * CHUNK_MS / 1000),
            callback=callback,
        )
        await super().open()
        self.started_at = time.time()
        self._stream.start()

    def _offer(self, chunk: bytes) -> None:
        """Queue a chunk on the event loop thread, shedding the oldest first."""
        queue = self._queue
        if queue is None:
            return
        if queue.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                queue.get_nowait()
                self.dropped_chunks += 1
        queue.put_nowait(chunk)

    async def __aiter__(self) -> AsyncIterator[bytes]:
        if self._stream is None:
            await self.open()
        assert self._queue is not None
        while True:
            yield await self._queue.get()

    async def close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            with contextlib.suppress(Exception):
                stream.stop()
            with contextlib.suppress(Exception):
                stream.close()
        await super().close()


class FileSource(AudioSource):
    """A recording, delivered exactly as a microphone would deliver it.

    `realtime=True` paces chunks at 100 ms intervals, which is how a rehearsal
    behaves and the only honest way to judge caption lag. `realtime=False`
    runs as fast as the decoder, which is what tests use.

    WAV is read with the standard library so the test suite needs nothing
    installed; every other format goes through ffmpeg, the same decoder
    `tools/probe_soniox.py` already relies on.
    """

    kind = "file"

    #: Formats handled without a subprocess.
    NATIVE_SUFFIXES = {".wav", ".wave"}

    def __init__(
        self,
        path: str | Path,
        *,
        realtime: bool = True,
        channel: int = 1,
        record: bool | Path = False,
    ) -> None:
        super().__init__(record=record)
        self.path = Path(path)
        self.realtime = realtime
        self.channel = max(1, channel)
        self.source_rate = SAMPLE_RATE
        self._proc: subprocess.Popen | None = None

    def describe(self) -> str:
        pace = "real time" if self.realtime else "fast"
        return f"{self.path.name} ({pace})"

    def status(self) -> dict[str, Any]:
        return {
            **super().status(),
            "file": self.path.name,
            "realtime": self.realtime,
            "total_ms": self.total_ms,
        }

    @property
    def total_ms(self) -> int | None:
        """Length of the file, when it is cheap to know. None via ffmpeg."""
        if self.path.suffix.lower() in self.NATIVE_SUFFIXES:
            try:
                with contextlib.closing(wave.open(str(self.path), "rb")) as wav:
                    return round(wav.getnframes() / wav.getframerate() * 1000)
            except Exception:
                return None
        return None

    # -- decoding ----------------------------------------------------------

    def _wav_blocks(self):
        """Yield int16 mono blocks from a WAV file at its own rate."""
        with contextlib.closing(wave.open(str(self.path), "rb")) as wav:
            if wav.getsampwidth() != 2:
                raise ValueError(
                    f"{self.path.name} is {wav.getsampwidth() * 8}-bit; "
                    "this reader handles 16-bit WAV. Convert it, or use any "
                    "format ffmpeg understands."
                )
            self.source_rate = wav.getframerate()
            channels = wav.getnchannels()
            pick = min(self.channel - 1, channels - 1)
            block_frames = max(1, int(self.source_rate * CHUNK_MS / 1000))
            while True:
                raw = wav.readframes(block_frames)
                if not raw:
                    return
                samples = np.frombuffer(raw, dtype="<i2")
                if channels > 1:
                    samples = samples.reshape(-1, channels)[:, pick]
                yield samples

    def _ffmpeg_blocks(self):
        """Yield int16 blocks already decoded to 16 kHz mono by ffmpeg."""
        if not shutil.which("ffmpeg"):
            raise RuntimeError(
                f"ffmpeg is not on PATH, and it is needed to decode "
                f"{self.path.name}. Install it (winget install Gyan.FFmpeg) "
                f"or use a 16-bit WAV."
            )
        self.source_rate = SAMPLE_RATE
        self._proc = subprocess.Popen(
            [
                "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                "-i", str(self.path),
                "-f", "s16le", "-acodec", "pcm_s16le",
                "-ac", "1", "-ar", str(SAMPLE_RATE), "-",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        assert self._proc.stdout is not None
        try:
            while True:
                raw = self._proc.stdout.read(CHUNK_BYTES)
                if not raw:
                    return
                yield np.frombuffer(raw, dtype="<i2")
        finally:
            self._stop_ffmpeg()

    def _stop_ffmpeg(self) -> None:
        """Reap the decoder on every exit path.

        `docs/soniox-notes.md` records what skipping this costs on Windows: a
        terminated-but-undrained ffmpeg never exits, and the symptom is a tool
        that hangs after printing the error it was trying to report.
        """
        proc, self._proc = self._proc, None
        if proc is None:
            return
        if proc.stdout is not None:
            with contextlib.suppress(Exception):
                proc.stdout.close()
        if proc.poll() is None:
            with contextlib.suppress(Exception):
                proc.terminate()
        with contextlib.suppress(Exception):
            proc.wait(timeout=5)
        if proc.poll() is None:
            with contextlib.suppress(Exception):
                proc.kill()

    # -- capture -----------------------------------------------------------

    async def __aiter__(self) -> AsyncIterator[bytes]:
        native = self.path.suffix.lower() in self.NATIVE_SUFFIXES
        blocks = self._wav_blocks() if native else self._ffmpeg_blocks()
        resampler: _Resampler | None = None
        if self._recorder is None:
            # Normally opened by `open()`; covered here for a FileSource
            # iterated directly, which is what most of the tests do.
            self._open_recorder()
        self.started_at = time.time()
        start = time.perf_counter()
        try:
            for samples in blocks:
                if resampler is None:
                    # Built after the first block: the WAV reader learns the
                    # file's rate while reading its header, not before.
                    resampler = _Resampler(self.source_rate)
                for chunk in self._emit(resampler.push(samples)):
                    if self.realtime:
                        target = self.chunks_out * CHUNK_MS / 1000
                        delay = target - (time.perf_counter() - start)
                        if delay > 0:
                            await asyncio.sleep(delay)
                    yield chunk
                    # Decoding is synchronous; without this a non-realtime
                    # file would starve the event loop and the viewer hub
                    # would not broadcast until the file ran out.
                    if not self.realtime:
                        await asyncio.sleep(0)
            # Whatever is left is shorter than a chunk. Pad it rather than
            # drop it, so a file's last syllable is not silently truncated.
            if self._pending:
                tail = bytes(self._pending).ljust(CHUNK_BYTES, b"\x00")
                self._pending.clear()
                self.chunks_out += 1
                if self._recorder is not None:
                    self._recorder.write(tail)
                yield tail
        finally:
            if not native:
                self._stop_ffmpeg()

    async def close(self) -> None:
        self._stop_ffmpeg()
        await super().close()


# -- device listing --------------------------------------------------------


def input_devices() -> list[dict[str, Any]]:
    """Input devices, as `tools/list_devices.py` and `/admin` show them.

    Returns an empty list rather than raising when there is no audio stack at
    all (a headless box, a machine with PortAudio missing): the operator page
    must still load and start a replay in that case.
    """
    try:
        import sounddevice as sd

        devices = sd.query_devices()
        try:
            default_in = sd.default.device[0]
        except Exception:
            default_in = None
    except Exception:
        return []

    out: list[dict[str, Any]] = []
    for index, info in enumerate(devices):
        channels = int(info.get("max_input_channels", 0))
        if channels <= 0:
            continue
        out.append({
            "index": index,
            "name": str(info.get("name", "")).strip(),
            "channels": channels,
            "sample_rate": int(round(float(info.get("default_samplerate") or 0))),
            "api": str(info.get("hostapi_name", "")),
            "default": index == default_in,
        })
    return out


def describe_devices() -> list[dict[str, Any]]:
    """`input_devices()` with the host API name resolved.

    `query_devices()` gives a host API *index*; the name is what tells an
    operator whether they are looking at the same microphone three times
    (MME, DirectSound, WASAPI all list it) or three different inputs.
    """
    devices = input_devices()
    if not devices:
        return devices
    try:
        import sounddevice as sd

        apis = sd.query_hostapis()
        raw = sd.query_devices()
        for device in devices:
            api_index = raw[device["index"]].get("hostapi")
            if isinstance(api_index, int) and 0 <= api_index < len(apis):
                device["api"] = str(apis[api_index].get("name", ""))
    except Exception:
        pass
    return devices


def force_utf8_output() -> None:
    """Make stdout survive a device name with a non-ASCII character.

    Windows gives this machine's array microphone a name containing `(R)` as
    a registered-sign, and a redirected stdout defaults to cp1252, so simply
    printing the device list crashed `tools/probe_soniox.py` in Phase 1
    (`docs/soniox-notes.md`). Same fix, same reason.
    """
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.reconfigure(encoding="utf-8", errors="replace")
