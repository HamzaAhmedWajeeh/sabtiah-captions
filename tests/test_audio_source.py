"""Phase 4: audio capture. Offline, and with no microphone.

Every test here drives `FileSource` over WAV files written in a tmp
directory, plus a fake `sounddevice` module for `MicSource`. That covers the
three things that silently corrupt audio - wrong chunk size, resampling that
clicks at block boundaries, and a channel picked by downmixing instead of
selection - without needing the venue's interface, or any interface.

`MicSource` is exercised through a stand-in for the `sounddevice` module
because the real one needs a device, and CI (and a laptop with the mic
switched off in Settings) has none. What the fake preserves is the part that
is easy to get wrong: the callback runs on *another thread* and must reach
the event loop through `call_soon_threadsafe`.
"""

from __future__ import annotations

import asyncio
import math
import sys
import threading
import wave
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.audio_source import (  # noqa: E402
    CHUNK_BYTES,
    CHUNK_MS,
    SAMPLE_RATE,
    AudioSource,
    FileSource,
    Level,
    MicSource,
    _Resampler,
    input_devices,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def sine(seconds: float, freq: float = 440.0, rate: int = SAMPLE_RATE,
         amplitude: float = 0.5, channels: int = 1) -> np.ndarray:
    """Interleaved int16 sine. Channel n carries freq * (n + 1)."""
    t = np.arange(int(seconds * rate)) / rate
    per_channel = [
        np.sin(2 * math.pi * freq * (c + 1) * t) * amplitude * 32767
        for c in range(channels)
    ]
    stacked = np.stack(per_channel, axis=1)
    return np.rint(stacked).astype(np.int16).reshape(-1)


def write_wav(path: Path, samples: np.ndarray, rate: int = SAMPLE_RATE,
              channels: int = 1) -> Path:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(samples.astype("<i2").tobytes())
    return path


async def drain(source) -> list[bytes]:
    return [chunk async for chunk in source]


def dominant_freq(chunk: bytes, rate: int = SAMPLE_RATE) -> float:
    """Peak frequency of a chunk, for checking resampling kept the tone."""
    samples = np.frombuffer(chunk, dtype="<i2").astype(np.float64)
    spectrum = np.abs(np.fft.rfft(samples * np.hanning(samples.size)))
    return float(np.fft.rfftfreq(samples.size, 1 / rate)[np.argmax(spectrum)])


def tone_level(chunk: bytes, freq: float, rate: int = SAMPLE_RATE) -> float:
    """How much energy a chunk holds near `freq`.

    `dominant_freq` alone is a weak test of channel selection: a downmix of
    two equally loud tones still has one of them as its argmax, so the
    assertion passes while both channels are present. Comparing the level at
    the wanted tone against the level at the *other* channel's tone is what
    actually distinguishes selection from summing.
    """
    samples = np.frombuffer(chunk, dtype="<i2").astype(np.float64)
    spectrum = np.abs(np.fft.rfft(samples * np.hanning(samples.size)))
    freqs = np.fft.rfftfreq(samples.size, 1 / rate)
    band = (freqs >= freq - 40) & (freqs <= freq + 40)
    return float(spectrum[band].max()) if band.any() else 0.0


# ---------------------------------------------------------------------------
# Level
# ---------------------------------------------------------------------------


def test_level_of_silence_is_floored_not_infinite():
    level = Level.of(np.zeros(1600, dtype=np.int16))
    assert level.rms == 0
    assert level.as_dict()["db"] == -60.0
    assert level.clipping is False


def test_level_of_full_scale_is_clipping():
    samples = np.full(1600, 32767, dtype=np.int16)
    level = Level.of(samples)
    assert level.peak == pytest.approx(1.0, abs=1e-4)
    assert level.clipping is True


def test_level_rms_matches_a_known_sine():
    # A sine at amplitude a has RMS a / sqrt(2).
    samples = np.frombuffer(sine(0.1, amplitude=0.5).tobytes(), dtype="<i2")
    level = Level.of(samples)
    assert level.rms == pytest.approx(0.5 / math.sqrt(2), rel=0.02)
    assert level.clipping is False


def test_clipping_latches_so_a_one_chunk_overload_is_seen():
    """A page polling at 1 Hz must not miss a clip that lasted 100 ms."""
    source = AudioSource()
    source._emit(np.full(1600, 32767, dtype=np.int16))
    assert source.level.clipping is True
    # A quiet chunk immediately after still reports clipping, because the
    # overload happened within the latch window.
    source._emit(np.zeros(1600, dtype=np.int16))
    assert source.level.clipping is True
    assert source.level.rms == 0


# ---------------------------------------------------------------------------
# Resampling
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rate", [8000, 16000, 22050, 44100, 48000])
def test_resampler_output_length_tracks_the_ratio(rate):
    resampler = _Resampler(rate)
    total = 0
    for _ in range(10):  # ten 100 ms blocks
        block = np.frombuffer(sine(0.1, rate=rate).tobytes(), dtype="<i2")
        total += resampler.push(block).size
    expected = SAMPLE_RATE  # one second of output
    # Within one output sample per block of start-up latency.
    assert abs(total - expected) <= 10


def test_resampler_keeps_the_tone_when_downsampling():
    resampler = _Resampler(48000)
    block = np.frombuffer(sine(0.5, freq=440, rate=48000).tobytes(),
                          dtype="<i2")
    out = resampler.push(block)
    assert dominant_freq(out.astype("<i2").tobytes()[:CHUNK_BYTES * 4]) \
        == pytest.approx(440, abs=20)


def test_resampler_has_no_discontinuity_at_block_boundaries():
    """The bug this guards: resampling each block independently.

    Independent blocks restart the interpolation phase every 100 ms, which
    puts a step in the waveform ten times a second. On a pure tone that shows
    up as a sample-to-sample jump far larger than the signal's own slope.
    """
    rate = 48000
    resampler = _Resampler(rate)
    full = np.frombuffer(sine(1.0, freq=300, rate=rate).tobytes(), dtype="<i2")
    block_size = rate * CHUNK_MS // 1000
    out = np.concatenate([
        resampler.push(full[i:i + block_size])
        for i in range(0, full.size, block_size)
    ])
    diffs = np.abs(np.diff(out.astype(np.int32)))
    # The largest step anywhere must stay close to the typical step of a
    # 300 Hz tone at 16 kHz; a boundary click is several times larger.
    assert diffs.max() < np.percentile(diffs, 99) * 2.5


def test_resampler_is_a_passthrough_at_the_target_rate():
    resampler = _Resampler(SAMPLE_RATE)
    block = np.frombuffer(sine(0.1).tobytes(), dtype="<i2")
    assert np.array_equal(resampler.push(block), block)


# ---------------------------------------------------------------------------
# FileSource
# ---------------------------------------------------------------------------


async def test_file_source_emits_exact_100ms_chunks(tmp_path):
    path = write_wav(tmp_path / "a.wav", sine(1.0))
    source = FileSource(path, realtime=False)
    chunks = await drain(source)
    assert len(chunks) == 10
    assert {len(c) for c in chunks} == {CHUNK_BYTES}
    assert source.audio_ms == 1000


async def test_file_source_pads_a_partial_final_chunk(tmp_path):
    """A file is rarely a whole number of 100 ms chunks.

    The last fragment is padded rather than dropped: a short frame sent to
    Soniox is silence spliced into speech, and dropping it loses the last
    syllable of the talk.
    """
    path = write_wav(tmp_path / "a.wav", sine(0.25))  # 2.5 chunks
    chunks = await drain(FileSource(path, realtime=False))
    assert len(chunks) == 3
    assert {len(c) for c in chunks} == {CHUNK_BYTES}
    tail = np.frombuffer(chunks[-1], dtype="<i2")
    assert np.all(tail[800:] == 0)  # second half is the padding


async def test_file_source_resamples_a_44k_file(tmp_path):
    path = write_wav(tmp_path / "a.wav", sine(1.0, rate=44100), rate=44100)
    source = FileSource(path, realtime=False)
    chunks = await drain(source)
    # One second in, one second out, whatever the file's rate.
    assert abs(len(chunks) - 10) <= 1
    assert dominant_freq(chunks[4]) == pytest.approx(440, abs=20)


async def test_file_source_picks_a_channel_instead_of_downmixing(tmp_path):
    """Channel 2 of this file is 880 Hz; channel 1 is 440 Hz.

    Downmixing would leave both tones present. The venue interface is
    multi-channel, so taking one channel rather than a sum is the difference
    between the speaker's microphone and the speaker's microphone plus
    whatever else is plugged in.
    """
    path = write_wav(tmp_path / "stereo.wav", sine(1.0, channels=2),
                     channels=2)
    chunks = await drain(FileSource(path, realtime=False, channel=2))
    assert dominant_freq(chunks[4]) == pytest.approx(880, abs=20)
    chunks = await drain(FileSource(path, realtime=False, channel=1))
    assert dominant_freq(chunks[4]) == pytest.approx(440, abs=20)


async def test_file_source_realtime_paces_at_wall_clock(tmp_path):
    path = write_wav(tmp_path / "a.wav", sine(0.5))
    loop = asyncio.get_running_loop()
    start = loop.time()
    chunks = await drain(FileSource(path, realtime=True))
    elapsed = loop.time() - start
    assert len(chunks) == 5
    # Five chunks is 500 ms of audio and must take about that long. The
    # upper bound is loose because a loaded Windows box sleeps late.
    assert 0.4 <= elapsed <= 1.5


async def test_file_source_rejects_8bit_wav_with_a_readable_error(tmp_path):
    path = tmp_path / "eight.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(1)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(b"\x80" * 1600)
    with pytest.raises(ValueError, match="8-bit"):
        await drain(FileSource(path, realtime=False))


async def test_file_source_reports_its_length(tmp_path):
    path = write_wav(tmp_path / "a.wav", sine(2.0))
    source = FileSource(path, realtime=False)
    assert source.total_ms == 2000
    assert source.status()["source_kind"] == "file"


async def test_recording_round_trips_the_captured_audio(tmp_path):
    """--record must write exactly what was captured, at 16 kHz mono."""
    path = write_wav(tmp_path / "a.wav", sine(0.5, rate=48000), rate=48000)
    out = tmp_path / "recorded.wav"
    source = FileSource(path, realtime=False, record=out)
    chunks = await drain(source)
    await source.close()
    with wave.open(str(out), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == SAMPLE_RATE
        written = wav.readframes(wav.getnframes())
    assert written == b"".join(chunks)


async def test_recording_is_off_unless_asked_for(tmp_path):
    """Privacy default (CLAUDE.md): a run writes no audio by itself."""
    path = write_wav(tmp_path / "a.wav", sine(0.2))
    source = FileSource(path, realtime=False)
    await drain(source)
    assert source.recording_path is None
    assert list(tmp_path.glob("*.wav")) == [path]


# ---------------------------------------------------------------------------
# MicSource, over a fake sounddevice
# ---------------------------------------------------------------------------


class FakeStream:
    """Stands in for `sounddevice.RawInputStream`.

    Calls the callback from a worker thread, exactly as PortAudio does, so a
    `MicSource` that touched the event loop directly from the callback would
    fail here instead of at the venue.
    """

    def __init__(self, blocks: list[np.ndarray], **kwargs):
        self.blocks = blocks
        self.kwargs = kwargs
        self.callback = kwargs["callback"]
        self.started = False
        self.closed = False
        self._thread: threading.Thread | None = None

    def start(self):
        self.started = True
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()

    def _pump(self):
        self.callback_thread = threading.current_thread().name
        for block in self.blocks:
            raw = block.astype("<i2").tobytes()
            self.callback(raw, block.size, None, None)

    def stop(self):
        self.started = False

    def close(self):
        self.closed = True


class FakeSounddevice:
    """The handful of `sounddevice` names `MicSource` actually uses."""

    def __init__(self, blocks, *, rate=48000, channels=4, native_16k=False):
        self.blocks = blocks
        self.rate = rate
        self.channels = channels
        self.native_16k = native_16k
        self.stream: FakeStream | None = None
        self.checked: list[dict] = []

    def query_devices(self, device=None, kind=None):
        info = {
            "name": "Fake Array (Intel® Smart Sound)",
            "max_input_channels": self.channels,
            "default_samplerate": float(self.rate),
            "hostapi": 0,
        }
        return info if device is not None or kind else [info]

    def query_hostapis(self):
        return [{"name": "Windows WASAPI"}]

    def check_input_settings(self, **kwargs):
        self.checked.append(kwargs)
        if kwargs.get("samplerate") == SAMPLE_RATE and not self.native_16k:
            raise RuntimeError("device does not support 16 kHz")

    def RawInputStream(self, **kwargs):  # noqa: N802 - mirrors sounddevice
        self.stream = FakeStream(self.blocks, **kwargs)
        return self.stream

    class default:  # noqa: D106 - mirrors sounddevice.default.device
        device = (0, 1)


@pytest.fixture
def fake_sd(monkeypatch):
    """Install a fake `sounddevice` for the duration of one test."""
    def install(**kwargs):
        module = FakeSounddevice(**kwargs)
        monkeypatch.setitem(sys.modules, "sounddevice", module)
        return module
    return install


async def one_second_of_blocks(rate=48000, channels=4):
    block_frames = rate * CHUNK_MS // 1000
    blocks = []
    for _ in range(10):
        blocks.append(np.frombuffer(
            sine(block_frames / rate, rate=rate, channels=channels).tobytes(),
            dtype="<i2",
        ))
    return blocks


async def take(source, count: int, timeout: float = 10.0) -> list[bytes]:
    """First `count` chunks out of a source, with a deadline.

    The deadline matters: a regression that stops producing chunks would
    otherwise hang the whole suite on `async for` rather than fail.
    """
    async def pull():
        out: list[bytes] = []
        async for chunk in source:
            out.append(chunk)
            if len(out) >= count:
                return out
        return out
    return await asyncio.wait_for(pull(), timeout)


async def test_mic_source_converts_a_48k_four_channel_device(fake_sd):
    """The normal Windows path: no 16 kHz, four channels, take channel 1."""
    fake = fake_sd(blocks=await one_second_of_blocks())
    source = MicSource(18, channel=1)
    await source.open()
    assert source.device_rate == 48000
    assert source.device_channels == 4
    assert source.native is False
    assert "-> 16000 Hz" in source.describe()
    assert "ch 1/4" in source.describe()
    # It asked for 16 kHz mono first, and only then for the native format.
    assert fake.checked[0]["samplerate"] == SAMPLE_RATE
    assert fake.checked[1]["samplerate"] == 48000

    chunks = []
    async for chunk in source:
        chunks.append(chunk)
        if len(chunks) >= 8:
            break
    await source.close()
    assert {len(c) for c in chunks} == {CHUNK_BYTES}
    assert dominant_freq(chunks[4]) == pytest.approx(440, abs=25)
    assert fake.stream.closed is True


async def test_mic_source_uses_16k_mono_when_the_device_offers_it(fake_sd):
    blocks = await one_second_of_blocks(rate=SAMPLE_RATE, channels=1)
    fake_sd(blocks=blocks, rate=SAMPLE_RATE, channels=1, native_16k=True)
    source = MicSource(31)
    await source.open()
    assert source.native is True
    assert source.device_rate == SAMPLE_RATE
    assert "->" not in source.describe()
    await source.close()


async def test_mic_source_rejects_a_channel_the_device_does_not_have(fake_sd):
    fake_sd(blocks=[], channels=2)
    with pytest.raises(ValueError, match="channel"):
        await MicSource(0, channel=5).open()


async def test_mic_source_sheds_the_oldest_audio_when_nothing_reads(fake_sd):
    """A stalled consumer must not grow the queue without bound.

    In a live talk, audio that is already ten seconds late is worthless, so
    the queue is capped and the *oldest* chunk goes. The counter is what tells
    the operator it happened.
    """
    from app import audio_source

    fake = fake_sd(blocks=await one_second_of_blocks())
    monkey_queue = 4
    original = audio_source.QUEUE_CHUNKS
    audio_source.QUEUE_CHUNKS = monkey_queue
    try:
        source = MicSource(18)
        await source.open()
        # Let the callback thread deliver all ten chunks without consuming
        # any of them.
        for _ in range(100):
            await asyncio.sleep(0.01)
            if source.chunks_out >= 10 and source.dropped_chunks:
                break
        await source.close()
    finally:
        audio_source.QUEUE_CHUNKS = original
    assert source.chunks_out == 10
    assert source.dropped_chunks >= 10 - monkey_queue
    assert fake.stream.closed is True


async def test_mic_callback_reaches_the_loop_from_its_own_thread(fake_sd):
    """The callback runs on PortAudio's thread (CLAUDE.md).

    `asyncio.Queue.put_nowait` is not thread-safe, so the chunk has to cross
    via `call_soon_threadsafe`. This test fails if that is ever simplified
    away: the fake stream calls back from a worker thread, and the chunks
    still have to arrive.
    """
    fake = fake_sd(blocks=await one_second_of_blocks())
    source = MicSource(18)
    await source.open()

    received = []
    async for chunk in source:
        received.append(chunk)
        break
    await source.close()

    assert len(received[0]) == CHUNK_BYTES
    # The chunk was produced somewhere other than the event loop's thread.
    assert fake.stream.callback_thread != threading.main_thread().name


# ---------------------------------------------------------------------------
# MicSource with the venue's shape of device: a stereo USB interface fed from
# a mixer, running at 44.1 or 48 kHz. Left and right carry different signals
# here (440 Hz and 880 Hz), so a downmix, a wrong channel and an off-by-one
# are all distinguishable from each other.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rate", [44100, 48000])
@pytest.mark.parametrize(
    "channel, wanted, rejected",
    [(1, 440, 880), (2, 880, 440)],
    ids=["left", "right"],
)
async def test_mic_source_picks_one_channel_of_a_stereo_interface(
    fake_sd, rate, channel, wanted, rejected,
):
    """Channel 1 is left, channel 2 is right, at both interface rates.

    The audio reaching Soniox must be the one channel the operator chose,
    resampled to 16 kHz mono - not a sum of both. On a mixer feed the other
    channel can be anything: the room mic, the house music, a dead input.
    """
    fake = fake_sd(blocks=await one_second_of_blocks(rate=rate, channels=2),
                   rate=rate, channels=2)
    source = MicSource(7, channel=channel)
    await source.open()

    assert source.device_rate == rate
    assert source.device_channels == 2
    assert source.native is False
    assert f"ch {channel}/2" in source.describe()
    assert source.status()["resampling"] is True

    chunks = await take(source, 8)
    await source.close()

    # Exact 100 ms chunks, whatever ratio the resampler was handed.
    assert {len(c) for c in chunks} == {CHUNK_BYTES}
    assert source.audio_ms >= 800

    middle = chunks[4]
    assert dominant_freq(middle) == pytest.approx(wanted, abs=25)
    # And the other channel is gone, not merely quieter than the wanted one.
    assert tone_level(middle, rejected) < tone_level(middle, wanted) / 10


async def test_mic_source_resamples_a_stereo_interface_without_clicking(
    fake_sd,
):
    """One second in, one second out, and no boundary step in the waveform.

    `_Resampler` is tested directly for this, but only on blocks handed to it
    whole. Here the blocks arrive as the driver delivers them - one 100 ms
    callback at a time, after a channel has been sliced out of each - which
    is the arrangement that would show a resampler rebuilt per block.
    """
    fake = fake_sd(blocks=await one_second_of_blocks(rate=48000, channels=2),
                   rate=48000, channels=2)
    source = MicSource(7, channel=1)
    await source.open()
    chunks = await take(source, 10)
    await source.close()

    assert len(chunks) == 10  # a second of 48 kHz stereo is ten 16 kHz chunks
    joined = np.frombuffer(b"".join(chunks), dtype="<i2").astype(np.int32)
    diffs = np.abs(np.diff(joined))
    assert diffs.max() < np.percentile(diffs, 99) * 2.5
    assert fake.stream.kwargs["channels"] == 2
    assert fake.stream.kwargs["samplerate"] == 48000


async def test_mic_source_never_lets_the_driver_downmix_a_stereo_device(
    fake_sd,
):
    """A device that *does* offer 16 kHz must still be opened in stereo.

    This is the one that was broken. `_choose_format` asked for 16 kHz mono
    first and returned as soon as the driver agreed, so on an interface that
    supports 16 kHz the operator's channel setting was silently discarded and
    the driver summed both inputs. A mixer feed with the room mic on the
    other channel would have gone out over the captions with no sign of it on
    `/admin` - `describe()` did not even mention a channel.
    """
    blocks = await one_second_of_blocks(rate=SAMPLE_RATE, channels=2)
    fake = fake_sd(blocks=blocks, rate=SAMPLE_RATE, channels=2,
                   native_16k=True)
    source = MicSource(7, channel=2)
    await source.open()

    assert source.device_channels == 2
    assert "ch 2/2" in source.describe()
    # Still no resampling: 16 kHz stereo in, 16 kHz mono out.
    assert source.device_rate == SAMPLE_RATE
    assert source.native is True
    assert every_check_asked_for_stereo(fake)

    chunks = await take(source, 8)
    await source.close()

    middle = chunks[4]
    assert dominant_freq(middle) == pytest.approx(880, abs=25)
    assert tone_level(middle, 440) < tone_level(middle, 880) / 10


def every_check_asked_for_stereo(fake) -> bool:
    """No `check_input_settings` call offered the driver a mono downmix."""
    return bool(fake.checked) and all(
        check.get("channels") == 2 for check in fake.checked
    )


def test_input_devices_is_empty_without_an_audio_stack(monkeypatch):
    """No PortAudio must not stop the operator page from loading."""
    monkeypatch.setitem(sys.modules, "sounddevice", None)
    assert input_devices() == []
