"""Phase 4 through the server: audio sources, the meter, the ledger.

Kept in its own file so `tests/test_server.py` stays the Phase 3 contract.
Everything here runs offline: audio runs are driven by `FileSource` over a
WAV written in a tmp directory, which exercises the same `start_audio` path
the microphone uses - the only difference between them is where the samples
come from.

The ledger is quarantined per test for the reason given in
`test_server.quarantine_usage_log`: a run nobody performed must never appear
on the record of what the $10 bought.
"""

from __future__ import annotations

import asyncio
import json
import sys
import wave
from pathlib import Path

import numpy as np
import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import main as server  # noqa: E402
from app import audio_source  # noqa: E402
from app import usage  # noqa: E402
from app.audio_source import SAMPLE_RATE, FileSource  # noqa: E402
from app.main import (  # noqa: E402
    CaptionSession,
    admin_devices,
    admin_level,
    admin_start,
    admin_status,
    admin_stop,
    available_audio,
    resolve_audio,
)
from app.settings import settings  # noqa: E402
from test_soniox_client import (  # noqa: E402
    DropsOnce,
    ErrorsOnce,
    FakeServer,
    FakeSocket,
)

TOKEN = "s3cret-operator-token"


class PartialsThenDrop(FakeServer):
    """Sends an uncommitted partial, then drops - the grey-line case.

    A partial is text Soniox has not committed to. It is only ever cleared by
    the next response, so after a drop it sits grey on every phone until the
    reconnect finishes.
    """

    async def on_audio(self, socket: FakeSocket, chunk: bytes) -> None:
        self.acked_ms[socket] = self.acked_ms.get(socket, 0) + 100
        socket.reply({
            "tokens": [{
                "text": "uncommitted", "is_final": False, "language": "en",
                "translation_status": "original",
            }],
            "final_audio_proc_ms": self.acked_ms[socket],
        })
        if self.connections == 1 and len(socket.sent_audio) >= 2:
            socket.drop()


@pytest.fixture(autouse=True)
def admin_token(monkeypatch):
    monkeypatch.setattr(settings, "admin_token", TOKEN)
    return TOKEN


@pytest.fixture(autouse=True)
def quarantine_usage_log(tmp_path, monkeypatch):
    monkeypatch.setattr(usage, "LOG_DIR", tmp_path / "logs")
    return tmp_path / "logs"


@pytest.fixture(autouse=True)
async def fresh_session(monkeypatch):
    made = CaptionSession()
    monkeypatch.setattr(server, "session", made)
    yield made
    await made.shutdown()


@pytest.fixture
def recordings(tmp_path, monkeypatch):
    """A fake `recordings/` holding one short tone."""
    directory = tmp_path / "recordings"
    directory.mkdir()
    samples = np.rint(
        np.sin(2 * np.pi * 440 * np.arange(SAMPLE_RATE // 2) / SAMPLE_RATE)
        * 0.5 * 32767
    ).astype(np.int16)
    with wave.open(str(directory / "tone.wav"), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(samples.tobytes())
    # Both: `main` resolves a run's input file here, and `audio_source`
    # chooses where `--record` writes. Missing the second one would make a
    # test write audio into the operator's real `recordings/`.
    monkeypatch.setattr(server, "RECORDING_DIR", directory)
    monkeypatch.setattr(audio_source, "RECORDING_DIR", directory)
    return directory


async def finish(session, limit: int = 2000) -> None:
    for _ in range(limit):
        if not session.running:
            return
        await asyncio.sleep(0.005)
    raise AssertionError("run did not finish")


# ---------------------------------------------------------------------------
# Containment: a request still cannot name a path of its own choosing
# ---------------------------------------------------------------------------


def test_available_audio_lists_only_audio(recordings):
    (recordings / "notes.txt").write_text("not audio", encoding="utf-8")
    assert available_audio() == ["tone.wav"]


@pytest.mark.parametrize("name", [
    "../../.env",
    "C:/Windows/System32/config/SAM",
    "/etc/passwd",
    "..\\..\\.env",
])
def test_a_traversing_audio_name_cannot_escape_recordings(recordings, name):
    with pytest.raises(HTTPException) as raised:
        resolve_audio(name)
    # Either "not audio" (400) or "no such recording" (404). Never a read.
    assert raised.value.status_code in (400, 404)


def test_a_non_audio_suffix_is_refused(recordings):
    with pytest.raises(HTTPException) as raised:
        resolve_audio("secrets.yaml")
    assert raised.value.status_code == 400


# ---------------------------------------------------------------------------
# Starting an audio run
# ---------------------------------------------------------------------------


async def test_a_file_run_captures_audio_and_makes_no_captions(
    fresh_session, recordings
):
    """Phase 4 has the microphone but not the engine.

    The audio path works end to end - chunks, levels, counters - and the
    pages stay empty, because nothing is transcribing yet. Phase 5 is what
    joins the two.
    """
    await admin_start(token=TOKEN, source="file", file="tone.wav", paced=False)
    assert fresh_session.running is True
    assert fresh_session.mode == "file"

    await finish(fresh_session)

    assert fresh_session.error is None
    assert fresh_session.responses == 5  # 500 ms at 100 ms a chunk
    assert fresh_session.state.render("ar").strip() == ""
    assert fresh_session.state.render("en").strip() == ""
    assert fresh_session.state.live is False


async def test_an_audio_run_reports_a_level_while_it_runs(
    fresh_session, recordings
):
    await admin_start(token=TOKEN, source="file", file="tone.wav", paced=True)
    # Let a couple of chunks through so the meter has something to show.
    for _ in range(40):
        await asyncio.sleep(0.01)
        if fresh_session.level and fresh_session.level["rms"] > 0:
            break

    body = json.loads((await admin_level(token=TOKEN)).body)
    assert body["running"] is True
    assert body["level"]["rms"] > 0
    # A 0.5-amplitude sine is about -9 dBFS RMS, nowhere near clipping.
    assert body["level"]["clipping"] is False
    assert -15 < body["level"]["db"] < -3

    await admin_stop(token=TOKEN)
    assert json.loads((await admin_level(token=TOKEN)).body)["level"] is None


async def test_level_is_absent_for_a_replay(fresh_session):
    """A replay has captions but no audio, so it has no level to show."""
    await admin_start(token=TOKEN, capture="run5-english.jsonl", paced=False)
    body = json.loads((await admin_level(token=TOKEN)).body)
    assert body["level"] is None
    await admin_stop(token=TOKEN)


async def test_a_bad_source_name_is_a_400(fresh_session):
    with pytest.raises(HTTPException) as raised:
        await admin_start(token=TOKEN, source="telepathy")
    assert raised.value.status_code == 400
    assert fresh_session.running is False


async def test_a_mic_run_that_cannot_open_a_device_is_a_400_not_a_crash(
    fresh_session, monkeypatch
):
    """No microphone, or one Windows has blocked, must not look like a server
    fault - and must leave the session stopped rather than half-started."""
    monkeypatch.setitem(sys.modules, "sounddevice", None)
    with pytest.raises(HTTPException) as raised:
        await admin_start(token=TOKEN, source="mic", device=99)
    assert raised.value.status_code == 400
    assert "99" in raised.value.detail
    assert fresh_session.running is False
    assert fresh_session.state.live is False


async def test_a_file_run_requires_a_file(fresh_session):
    with pytest.raises(HTTPException) as raised:
        await admin_start(token=TOKEN, source="file")
    assert raised.value.status_code == 400


async def test_a_replay_still_requires_a_capture(fresh_session):
    """`source` defaulting to replay must not make `capture` optional."""
    with pytest.raises(HTTPException) as raised:
        await admin_start(token=TOKEN)
    assert raised.value.status_code == 400


async def test_starting_an_audio_run_replaces_a_replay(
    fresh_session, recordings
):
    await admin_start(token=TOKEN, capture="run5-english.jsonl", paced=True)
    first = fresh_session.state
    await admin_start(token=TOKEN, source="file", file="tone.wav", paced=False)
    assert fresh_session.mode == "file"
    assert fresh_session.state is not first  # captions cleared, not carried
    await finish(fresh_session)


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------


async def test_a_run_records_only_when_asked(fresh_session, recordings):
    await admin_start(token=TOKEN, source="file", file="tone.wav", paced=False)
    await finish(fresh_session)
    assert sorted(p.name for p in recordings.iterdir()) == ["tone.wav"]


async def test_record_true_writes_a_wav_into_recordings(
    fresh_session, recordings
):
    await admin_start(token=TOKEN, source="file", file="tone.wav",
                      paced=False, record=True)
    status = json.loads((await admin_status(token=TOKEN)).body)
    assert status["recording"]
    await finish(fresh_session)

    written = [p for p in recordings.iterdir() if p.name != "tone.wav"]
    assert len(written) == 1
    with wave.open(str(written[0]), "rb") as wav:
        assert wav.getframerate() == SAMPLE_RATE
        assert wav.getnframes() == SAMPLE_RATE // 2


# ---------------------------------------------------------------------------
# The ledger
# ---------------------------------------------------------------------------


async def test_a_finished_run_is_logged_as_free(
    fresh_session, recordings, quarantine_usage_log
):
    await admin_start(token=TOKEN, source="file", file="tone.wav", paced=False)
    await finish(fresh_session)

    totals = usage.totals(quarantine_usage_log)
    assert totals.runs == 1
    assert totals.billable_seconds == 0.0
    assert totals.free_seconds == 0.5
    assert totals.last["engine"] == "monitor"


async def test_a_run_stopped_early_still_logs_what_it_captured(
    fresh_session, recordings, quarantine_usage_log
):
    """The operator pressing Stop is the normal case, not an edge case.

    The ledger line is written from the feed's `finally`, so a cancelled run
    is recorded with the seconds it actually used - otherwise the one number
    the $10 budget depends on would only be right when a run was left to end
    on its own.
    """
    await admin_start(token=TOKEN, source="file", file="tone.wav", paced=True)
    for _ in range(40):
        await asyncio.sleep(0.01)
        if fresh_session.responses >= 2:
            break
    await admin_stop(token=TOKEN)

    totals = usage.totals(quarantine_usage_log)
    assert totals.runs == 1
    assert 0 < totals.free_seconds < 0.5


async def test_a_replay_is_logged_with_the_audio_it_represents(
    fresh_session, quarantine_usage_log
):
    """A replay spends nothing, and the ledger says so explicitly.

    It still records the two minutes of audio the capture stands for: the
    distinction the file has to make is between audio streamed and audio
    replayed, not between runs worth logging and runs worth forgetting.
    """
    await admin_start(token=TOKEN, capture="run5-english.jsonl", paced=False)
    await finish(fresh_session, limit=20_000)

    totals = usage.totals(quarantine_usage_log)
    assert totals.billable_seconds == 0.0
    assert totals.free_seconds == 120.0
    assert totals.last["billable"] is False


async def test_one_run_writes_exactly_one_ledger_line(
    fresh_session, recordings, quarantine_usage_log
):
    """`stop()` tidies up and the feed's `finally` logs; only one may write."""
    await admin_start(token=TOKEN, source="file", file="tone.wav", paced=True)
    await asyncio.sleep(0.05)
    await admin_stop(token=TOKEN)
    await admin_stop(token=TOKEN)  # a second Stop click
    assert usage.totals(quarantine_usage_log).runs == 1


async def test_status_carries_the_ledger_and_no_engine_name(
    fresh_session, quarantine_usage_log
):
    body = json.loads((await admin_status(token=TOKEN)).body)
    assert body["usage"]["runs"] == 0
    assert body["usage"]["billable_minutes"] == 0.0
    assert body["usage"]["budget_usd"] == 10.0
    # Same invariant as Phase 3: the status page names no engine and carries
    # no key (BUILD_SPEC section 8).
    assert "soniox" not in json.dumps(body).lower()


async def test_no_dollar_figure_is_shown_without_a_configured_rate(
    fresh_session, quarantine_usage_log
):
    body = json.loads((await admin_status(token=TOKEN)).body)
    assert body["usage"]["estimated_usd"] is None
    assert body["usage"]["rate_per_minute"] is None


# ---------------------------------------------------------------------------
# Devices
# ---------------------------------------------------------------------------


async def test_devices_endpoint_rechecks_the_token():
    with pytest.raises(HTTPException) as raised:
        await admin_devices(token="wrong")
    assert raised.value.status_code == 401


async def test_level_endpoint_rechecks_the_token():
    with pytest.raises(HTTPException) as raised:
        await admin_level(token=None)
    assert raised.value.status_code == 401


async def test_devices_endpoint_survives_a_machine_with_no_audio(monkeypatch):
    """The operator page must still load, and still be able to replay."""
    monkeypatch.setitem(sys.modules, "sounddevice", None)
    body = json.loads((await admin_devices(token=TOKEN)).body)
    assert body == {"devices": []}


# ---------------------------------------------------------------------------
# Phase 5 through the server: the one path that spends money
# ---------------------------------------------------------------------------


async def test_a_live_run_produces_captions_and_a_billable_ledger_line(
    fresh_session, recordings, quarantine_usage_log, monkeypatch
):
    """The whole pipeline, with the socket faked: audio -> engine -> captions.

    This is the only shape in the project that would cost money, so it is
    also the only place the ledger writes `billable: true`. The fake server
    is the one from `test_soniox_client`.
    """
    monkeypatch.setattr(settings, "soniox_api_key", "test-key")
    server = FakeServer()
    source = FileSource(recordings / "tone.wav", realtime=False)

    await fresh_session.start_live(source, connect=server, api_key="test-key")
    await finish(fresh_session)

    assert fresh_session.error is None
    assert fresh_session.state.render("en").strip().startswith("word")
    assert server.connections == 1

    totals = usage.totals(quarantine_usage_log)
    assert totals.billable_runs == 1
    assert totals.billable_seconds == pytest.approx(0.5, abs=0.2)
    assert totals.last["engine"] == "soniox"


async def test_a_live_run_without_a_key_is_refused_before_connecting(
    fresh_session, recordings, monkeypatch
):
    monkeypatch.setattr(settings, "soniox_api_key", "")
    with pytest.raises(HTTPException) as raised:
        await admin_start(token=TOKEN, source="live", device=0)
    assert raised.value.status_code == 400
    assert fresh_session.running is False


async def test_the_engine_state_reaches_the_pages_without_clearing_live(
    fresh_session, recordings, monkeypatch
):
    """BUILD_SPEC Phase 5: a reconnect must not look like the end of the talk.

    `live` is the operator's flag; `status` is the engine's. A page that went
    grey on every blink would look broken during the one event this project
    exists to survive.
    """
    monkeypatch.setattr(settings, "soniox_api_key", "test-key")
    server = DropsOnce(after=2)
    source = FileSource(recordings / "tone.wav", realtime=True)

    await fresh_session.start_live(source, connect=server, api_key="test-key")
    seen = set()
    for _ in range(200):
        await asyncio.sleep(0.01)
        seen.add(fresh_session.state.status)
        assert fresh_session.state.live is True, "live was cleared by a drop"
        if server.connections >= 2 and fresh_session.state.status == "live":
            break
    await admin_stop(token=TOKEN)

    assert "reconnecting" in seen, f"pages never saw a reconnect: {seen}"
    assert "live" in seen
    # Only the operator clears it.
    assert fresh_session.state.live is False


async def test_a_drop_commits_the_grey_text_rather_than_leaving_it_hanging(
    fresh_session, recordings, monkeypatch
):
    monkeypatch.setattr(settings, "soniox_api_key", "test-key")
    server = PartialsThenDrop()
    source = FileSource(recordings / "tone.wav", realtime=True)

    await fresh_session.start_live(source, connect=server, api_key="test-key")
    for _ in range(200):
        await asyncio.sleep(0.01)
        if server.connections >= 2:
            break
    await admin_stop(token=TOKEN)

    # The grey text was committed to `final`. Checking `partial` is empty
    # here would be wrong: the second connection is sending partials of its
    # own by now, which is exactly what it should be doing.
    paragraphs = fresh_session.state.streams["en"]
    assert any("uncommitted" in p.final for p in paragraphs), (
        "the partial was never committed: "
        + repr([(p.final, p.partial) for p in paragraphs])
    )


async def test_a_fatal_error_stops_the_session_and_shows_up_on_admin(
    fresh_session, recordings, monkeypatch
):
    """Acceptance 3: "admin shows a clear error, no retry loop"."""
    monkeypatch.setattr(settings, "soniox_api_key", "test-key")
    server = ErrorsOnce("unauthenticated")
    source = FileSource(recordings / "tone.wav", realtime=False)

    await fresh_session.start_live(source, connect=server, api_key="bad-key")
    await finish(fresh_session)

    assert server.connections == 1, "it retried a fatal error"
    body = json.loads((await admin_status(token=TOKEN)).body)
    assert "unauthenticated" in (body["error"] or "")
    assert body["engine_error"]["fatal"] is True
    assert body["running"] is False


async def test_the_served_status_still_names_no_engine_during_a_live_run(
    fresh_session, recordings, monkeypatch
):
    """The Phase 3 invariant has to survive the phase that adds the engine."""
    monkeypatch.setattr(settings, "soniox_api_key", "super-secret-key")
    server = FakeServer()
    source = FileSource(recordings / "tone.wav", realtime=True)

    await fresh_session.start_live(source, connect=server, api_key="super-secret-key")
    await asyncio.sleep(0.05)
    body = json.loads((await admin_status(token=TOKEN)).body)
    await admin_stop(token=TOKEN)

    dumped = json.dumps(body).lower()
    assert "soniox" not in dumped
    assert "super-secret-key" not in dumped
    assert "bearer" not in dumped
    # and it does carry what the operator needs
    assert body["engine_state"] in ("connecting", "streaming", "stopped")
    assert "lag_seconds" in body and "streamed_seconds" in body


async def test_stopping_a_live_run_closes_the_socket_and_the_device(
    fresh_session, recordings, monkeypatch
):
    """The stream must stop when the session stops (CLAUDE.md)."""
    monkeypatch.setattr(settings, "soniox_api_key", "test-key")
    server = FakeServer()
    source = FileSource(recordings / "tone.wav", realtime=True)

    await fresh_session.start_live(source, connect=server, api_key="test-key")
    await asyncio.sleep(0.05)
    await admin_stop(token=TOKEN)

    assert fresh_session.running is False
    assert fresh_session.state.live is False
    assert fresh_session.state.status == "idle"
    assert server.sockets[0]._done is True
    streamed = json.loads((await admin_status(token=TOKEN)).body)
    assert streamed["running"] is False
