"""Print the audio input devices, so the operator can name one.

    uv run python tools/list_devices.py
    uv run python tools/list_devices.py --probe      # also test 16 kHz mono
    uv run python tools/list_devices.py --listen 3   # 3 s level check

BUILD_SPEC section 6, Phase 4. Spends no Soniox credit - it never connects to
anything. `--listen` opens the microphone locally and prints a level meter,
which is the fastest way to answer the only two questions that matter before a
talk: is this the right input, and is the gain sane.

On Windows the same physical microphone appears several times, once per host
API (MME, DirectSound, WASAPI). WASAPI is usually the one to pick: it is the
modern path, and MME imposes extra latency that lands on top of the caption
delay the audience already sees.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.audio_source import (  # noqa: E402
    SAMPLE_RATE,
    Level,
    MicSource,
    describe_devices,
    force_utf8_output,
)


def print_devices(probe: bool) -> int:
    devices = describe_devices()
    if not devices:
        print(
            "No input devices found. Either this machine has no audio input, "
            "or PortAudio is missing - check `uv run python -c \"import "
            "sounddevice; print(sounddevice.query_devices())\"`.",
            file=sys.stderr,
        )
        return 1

    print(f"{len(devices)} input device(s). 16 kHz mono is what Soniox is fed.")
    print()
    header = f"{'idx':>4}  {'ch':>3}  {'default Hz':>10}  {'api':<14}  name"
    print(header)
    print("-" * max(len(header), 60))
    for device in devices:
        mark = "*" if device["default"] else " "
        print(
            f"{device['index']:>4}{mark} {device['channels']:>3}  "
            f"{device['sample_rate']:>10}  {device['api'][:14]:<14}  "
            f"{device['name']}"
        )
    print()
    print("* = system default input.  Use the index with --listen or /admin.")

    if probe:
        print()
        print(f"Probing each device for {SAMPLE_RATE} Hz mono int16:")
        import sounddevice as sd

        for device in devices:
            try:
                sd.check_input_settings(
                    device=device["index"], channels=1,
                    samplerate=SAMPLE_RATE, dtype="int16",
                )
                verdict = "native 16 kHz mono"
            except Exception as exc:
                # Not a failure: capturing at the device rate and converting
                # in `audio_source._Resampler` is the normal path.
                verdict = f"needs conversion ({type(exc).__name__})"
            print(f"  {device['index']:>4}  {device['name'][:40]:<40}  {verdict}")
    return 0


def meter(level: Level, width: int = 32) -> str:
    """A text VU bar. `rms` fills it; a clipped peak is shown as `!`."""
    db = max(-60.0, 20 * math.log10(level.rms) if level.rms > 0 else -60.0)
    filled = int(round((db + 60) / 60 * width))
    bar = "#" * filled + "." * (width - filled)
    flag = " CLIP" if level.clipping else ""
    return f"[{bar}] {db:6.1f} dBFS  peak {level.peak:.3f}{flag}"


async def listen(device: int | None, seconds: float, channel: int) -> int:
    """Open the microphone and print its level until `seconds` elapse."""
    import asyncio

    source = MicSource(device, channel=channel)
    try:
        await source.open()
    except Exception as exc:
        print(f"Could not open device {device}: {exc}", file=sys.stderr)
        return 1

    print(f"Listening to {source.describe()} for {seconds:.0f} s. Speak.")
    deadline = time.monotonic() + seconds
    chunks = 0
    try:
        async for _ in source:
            chunks += 1
            if chunks % 2 == 0:  # ~5 Hz, readable in a terminal
                print("\r" + meter(source.level), end="", flush=True)
            if time.monotonic() >= deadline:
                break
    except asyncio.CancelledError:
        pass
    finally:
        await source.close()
    print()
    print(
        f"{source.audio_seconds:.1f} s captured, "
        f"{source.dropped_chunks} chunk(s) dropped, "
        f"{source.callback_errors} callback warning(s)."
    )
    if source.audio_seconds and source.level.rms < 0.001:
        print(
            "Level never rose above silence. Wrong input, a muted device, or "
            "Windows denying microphone access to the terminal "
            "(Settings > Privacy & security > Microphone)."
        )
    return 0


def main() -> int:
    force_utf8_output()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--probe", action="store_true",
        help="test every device for native 16 kHz mono support",
    )
    parser.add_argument(
        "--listen", nargs="?", type=float, const=5.0, default=None,
        metavar="SECONDS", help="open a device and show its level (default 5 s)",
    )
    parser.add_argument(
        "--device", type=int, default=None,
        help="device index for --listen (default: system default input)",
    )
    parser.add_argument(
        "--channel", type=int, default=1,
        help="which input channel to take, 1-based (default 1)",
    )
    args = parser.parse_args()

    if args.listen is not None:
        import asyncio

        return asyncio.run(listen(args.device, args.listen, args.channel))
    return print_devices(args.probe)


if __name__ == "__main__":
    raise SystemExit(main())
