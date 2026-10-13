"""Session details are private: they must not escape /admin.

Speaker names, the talk title and the operator's notes are not public, and
this repository is. They are allowed in exactly two places:

1. the Soniox `context` object, which is what makes names come out spelled
   right, and
2. `/admin`, behind its token.

Everything else - the audience page, the big screen, the viewer socket, the
status poll, `logs/usage.jsonl`, the raw response log, saved captures, and
error messages - must carry **counts at most**. These tests use values that
cannot occur by accident, then go looking for them everywhere a byte can
leave the process.

`config/session.yaml` itself is gitignored; `test_session_file_is_not_tracked`
checks that it stayed that way.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import main as server  # noqa: E402
from app import usage  # noqa: E402
from app.audio_source import FileSource  # noqa: E402
from app.main import (  # noqa: E402
    CaptionSession,
    admin_apply_session,
    admin_save_session,
    admin_session,
    admin_status,
    admin_stop,
    asset,
    index,
    screen,
    viewer_socket,
)
from app.settings import SessionContext, WEB_DIR, settings  # noqa: E402

TOKEN = "s3cret-operator-token"

#: Strings that cannot occur by chance, one per field.
SECRETS = {
    "title": "ZZTITLEZZ-quantum-basket-weaving",
    "speaker": "ZZSPEAKERZZ-Ingeborg-Qvistgaard",
    "company": "ZZCOMPANYZZ-Fnordlich-GmbH",
    "term": "ZZTERMZZ-hyperflange",
    "note": "ZZNOTEZZ-he-hates-being-called-Ingy",
}

PRIVATE = SessionContext(
    title=SECRETS["title"],
    speakers=[SECRETS["speaker"]],
    companies=[SECRETS["company"]],
    terms=[SECRETS["term"]],
    notes=SECRETS["note"],
)


def leaked(blob: str) -> list[str]:
    """Which secret values appear in `blob`."""
    return sorted(name for name, value in SECRETS.items() if value in blob)


@pytest.fixture(autouse=True)
def admin_token(monkeypatch):
    monkeypatch.setattr(settings, "admin_token", TOKEN)
    return TOKEN


@pytest.fixture(autouse=True)
def quarantine(tmp_path, monkeypatch):
    """Never touch the operator's real files."""
    monkeypatch.setattr(usage, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(server, "LOG_DIR", tmp_path / "logs")
    import shutil

    import app.settings as settings_module
    config = tmp_path / "config"
    config.mkdir()
    # The real glossary, so the merge with session terms is tested against
    # the vocabulary that actually ships.
    shutil.copy(ROOT / "config" / "glossary.yaml", config / "glossary.yaml")
    monkeypatch.setattr(settings_module, "CONFIG_DIR", config)
    return tmp_path


@pytest.fixture
def recordings_dir(tmp_path, monkeypatch):
    """A short tone to drive the audio path with."""
    import wave

    import numpy as np

    directory = tmp_path / "recordings"
    directory.mkdir()
    samples = np.rint(
        np.sin(2 * np.pi * 440 * np.arange(8000) / 16000) * 0.5 * 32767
    ).astype(np.int16)
    with wave.open(str(directory / "tone.wav"), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(samples.tobytes())
    monkeypatch.setattr(server, "RECORDING_DIR", directory)
    import app.audio_source as audio_source
    monkeypatch.setattr(audio_source, "RECORDING_DIR", directory)
    return directory


@pytest.fixture
async def loaded_session(monkeypatch, quarantine):
    """A session holding the private details."""
    made = CaptionSession()
    monkeypatch.setattr(server, "session", made)
    made.save_details(PRIVATE)
    yield made
    await made.shutdown()


# ---------------------------------------------------------------------------
# The file itself
# ---------------------------------------------------------------------------


def test_the_session_file_is_not_tracked_by_git():
    """The whole point. If this fails, a name is one commit from public."""
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "config/session.yaml"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert result.returncode != 0, (
        "config/session.yaml is tracked by git; it holds speaker names and "
        "this repository is public"
    )


def test_the_session_file_is_ignored_not_merely_absent():
    """Untracked is not enough - the next `git add -A` would catch it."""
    result = subprocess.run(
        ["git", "check-ignore", "config/session.yaml"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, "config/session.yaml is not gitignored"


def test_the_example_file_is_committed_and_empty():
    example = ROOT / "config" / "session.example.yaml"
    assert example.exists(), "config/session.example.yaml is missing"
    import yaml

    loaded = yaml.safe_load(example.read_text(encoding="utf-8")) or {}
    assert set(loaded) == {"title", "speakers", "companies", "terms", "notes"}
    assert not any(loaded.values()), f"the example carries values: {loaded}"


def test_a_missing_session_file_is_not_an_error(tmp_path, monkeypatch):
    """A fresh clone has none, and the server must start anyway."""
    import app.settings as settings_module
    monkeypatch.setattr(settings_module, "CONFIG_DIR", tmp_path / "nowhere")
    made = CaptionSession()
    assert made.details.is_empty()
    assert made.context()["general"] == []


# ---------------------------------------------------------------------------
# Public surfaces
# ---------------------------------------------------------------------------


async def test_the_audience_page_carries_no_session_details(loaded_session):
    for page in (await index(), await screen()):
        blob = Path(page.path).read_text(encoding="utf-8")
        assert not leaked(blob), f"{page.path} leaks {leaked(blob)}"


async def test_no_served_asset_carries_session_details(loaded_session):
    for name in ("style.css", "app.js", "screen.js", "admin.js"):
        blob = Path((await asset(name)).path).read_text(encoding="utf-8")
        assert not leaked(blob), f"{name} leaks {leaked(blob)}"


class FakeWebSocket:
    """A viewer. Records every frame the server sends it."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.accepted = False

    async def accept(self) -> None:
        self.accepted = True

    async def send_text(self, text: str) -> None:
        self.sent.append(text)

    async def receive(self) -> dict:
        return {"type": "websocket.disconnect", "code": 1000}

    async def close(self, code: int = 1000) -> None:
        pass


async def test_the_viewer_socket_carries_no_session_details(loaded_session):
    """The snapshot a phone receives on connect, and every diff after it."""
    loaded_session.state.add_response({"tokens": [
        {"text": "some speech", "is_final": True, "language": "en",
         "translation_status": "original"}
    ]})
    socket = FakeWebSocket()
    await viewer_socket(socket)

    blob = "".join(socket.sent)
    assert blob, "the socket sent nothing; this test proved nothing"
    assert not leaked(blob), f"the viewer socket leaks {leaked(blob)}"
    snapshot = json.loads(socket.sent[0])
    assert "details" not in snapshot
    assert set(snapshot) == {"type", "seq", "live", "status", "streams"}


async def test_the_status_poll_carries_counts_not_values(loaded_session):
    body = (await admin_status(token=TOKEN)).body.decode("utf-8")
    assert not leaked(body), f"/admin/status leaks {leaked(body)}"

    status = json.loads(body)
    # ...but it does say how much there is, which is what the page shows.
    assert status["details"] == {
        "title": True, "speakers": 1, "companies": 1, "terms": 1,
        "notes_chars": len(SECRETS["note"]),
    }


async def test_only_the_session_route_returns_the_values(loaded_session):
    """One route, behind the token, and it is the one the form reads."""
    body = (await admin_session(token=TOKEN)).body.decode("utf-8")
    assert sorted(SECRETS) == leaked(body), (
        "/admin/session must return every field - it is what the form loads"
    )


@pytest.mark.parametrize("handler", [admin_session, admin_status])
async def test_the_session_routes_need_the_token(loaded_session, handler):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as raised:
        await handler(token="wrong")
    assert raised.value.status_code == 401


# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------


async def test_the_usage_ledger_carries_no_session_details(
    loaded_session, recordings_dir, quarantine
):
    """`logs/usage.jsonl` is kept for months and read long after the talk."""
    source = FileSource(recordings_dir / "tone.wav", realtime=False)
    await loaded_session.start_audio(source)
    for _ in range(300):
        await asyncio.sleep(0.005)
        if not loaded_session.running:
            break

    ledger = usage.usage_path(quarantine / "logs")
    blob = ledger.read_text(encoding="utf-8")
    assert blob, "nothing was logged; this test proved nothing"
    assert not leaked(blob), f"the usage ledger leaks {leaked(blob)}"


async def test_the_response_log_carries_no_session_details(
    loaded_session, recordings_dir, quarantine, monkeypatch
):
    """The raw capture records what Soniox *sent back*, never what we sent."""
    from test_soniox_client import FakeServer

    monkeypatch.setattr(settings, "soniox_api_key", "test-key")
    fake = FakeServer()
    source = FileSource(recordings_dir / "tone.wav", realtime=False)
    await loaded_session.start_live(source, connect=fake, api_key="test-key",
                                    capture=True)
    for _ in range(400):
        await asyncio.sleep(0.005)
        if not loaded_session.running:
            break
    await admin_stop(token=TOKEN)

    written = list((quarantine / "logs").glob("live-*.jsonl"))
    assert len(written) == 1, f"no capture written: {written}"
    blob = written[0].read_text(encoding="utf-8")
    assert not leaked(blob), f"the response log leaks {leaked(blob)}"


def test_committed_captures_carry_no_session_details():
    """Fixtures are committed; they must never have absorbed a context."""
    for path in (ROOT / "tests" / "fixtures").glob("*.jsonl"):
        blob = path.read_text(encoding="utf-8")
        assert not leaked(blob), f"{path.name} leaks {leaked(blob)}"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


async def test_a_context_failure_does_not_put_details_in_the_error(
    loaded_session, monkeypatch
):
    """A traceback or an error string is a leak like any other.

    The engine's error is rendered into `session.error` and shown on /admin,
    but it also ends up in the status payload - so even there the values must
    not appear.
    """
    monkeypatch.setattr(settings, "soniox_api_key", "")
    from fastapi import HTTPException

    source = FileSource(ROOT / "tests" / "fixtures" / "run1.jsonl")
    with pytest.raises(HTTPException) as raised:
        await loaded_session.start_live(source)
    assert not leaked(str(raised.value.detail))
    body = (await admin_status(token=TOKEN)).body.decode("utf-8")
    assert not leaked(body)


# ---------------------------------------------------------------------------
# The context itself: the one place they are meant to go
# ---------------------------------------------------------------------------


async def test_the_details_do_reach_the_soniox_context(loaded_session):
    """The flip side of every test above - they have to arrive somewhere."""
    context = loaded_session.context()
    blob = json.dumps(context, ensure_ascii=False)
    assert sorted(SECRETS) == leaked(blob), (
        f"the context is missing fields: {sorted(set(SECRETS) - set(leaked(blob)))}"
    )
    assert {"key": "speaker", "value": SECRETS["speaker"]} in context["general"]
    assert SECRETS["term"] in context["terms"]
    # ...on top of the public glossary, not instead of it.
    assert "RAG" in context["terms"]


async def test_saving_empty_details_clears_everything(loaded_session):
    await admin_save_session(token=TOKEN, details=SessionContext())
    assert loaded_session.details.is_empty()
    assert loaded_session.context()["general"] == []
    assert loaded_session.context()["text"] == ""
    body = (await admin_session(token=TOKEN)).body.decode("utf-8")
    assert not leaked(body), "cleared details came back"


# ---------------------------------------------------------------------------
# Editing the details at runtime
# ---------------------------------------------------------------------------


async def test_saving_with_nothing_running_needs_no_apply(loaded_session):
    """The next Start picks it up; there is nothing to reconnect."""
    body = json.loads((await admin_save_session(
        token=TOKEN,
        details=SessionContext(title="later", speakers=["Someone Else"]),
    )).body)
    assert body["saved"] is True
    assert body["pending"] is False
    assert loaded_session.details_pending is False
    assert loaded_session.details.title == "later"


async def test_the_saved_file_round_trips_through_disk(loaded_session,
                                                       quarantine):
    """Saved, not merely held: the laptop may be restarted before the talk."""
    from app.settings import load_session

    await admin_save_session(
        token=TOKEN,
        details=SessionContext(title="On disk", speakers=["اسم عربي"]),
    )
    written = quarantine / "config" / "session.yaml"
    assert written.exists()
    reloaded = load_session(written)
    assert reloaded.title == "On disk"
    assert reloaded.speakers == ["اسم عربي"]
    # ...and the file says what it is, for whoever opens it in an editor.
    assert "LOCAL ONLY" in written.read_text(encoding="utf-8")


async def test_a_save_during_a_live_run_is_pending_until_applied(
    loaded_session, recordings_dir, monkeypatch
):
    """Reconnecting unasked would be a surprise mid-talk, and the operator
    may want to type several fields before any of it takes effect."""
    from test_soniox_client import FakeServer

    monkeypatch.setattr(settings, "soniox_api_key", "test-key")
    server_double = FakeServer()
    source = FileSource(recordings_dir / "tone.wav", realtime=True)
    await loaded_session.start_live(source, connect=server_double,
                                    api_key="test-key")
    await asyncio.sleep(0.05)

    body = json.loads((await admin_save_session(
        token=TOKEN, details=SessionContext(title="Changed mid-talk"),
    )).body)
    assert body["pending"] is True
    assert loaded_session.details_pending is True
    assert server_double.connections == 1, "it reconnected without being asked"

    status = json.loads((await admin_status(token=TOKEN)).body)
    assert status["details_pending"] is True
    await admin_stop(token=TOKEN)


async def test_apply_now_reconnects_and_sends_the_new_context(
    loaded_session, recordings_dir, monkeypatch
):
    """The point of the whole feature: Soniox takes its context in the config
    frame, so a changed speaker list only reaches it on a new connection."""
    from test_soniox_client import FakeServer

    monkeypatch.setattr(settings, "soniox_api_key", "test-key")
    server_double = FakeServer()
    source = FileSource(recordings_dir / "tone.wav", realtime=True)
    await loaded_session.start_live(source, connect=server_double,
                                    api_key="test-key")
    await asyncio.sleep(0.05)

    first_config = json.loads(server_double.sockets[0].sent_text[0])
    assert SECRETS["speaker"] in json.dumps(first_config, ensure_ascii=False)

    await admin_save_session(
        token=TOKEN,
        details=SessionContext(title="New title", speakers=["Second Speaker"],
                               terms=["newterm"]),
    )
    applied = json.loads((await admin_apply_session(token=TOKEN)).body)
    assert applied["applied"] is True

    for _ in range(300):
        await asyncio.sleep(0.01)
        if server_double.connections >= 2:
            break
    await admin_stop(token=TOKEN)

    assert server_double.connections >= 2, "apply did not reconnect"
    second_config = json.loads(server_double.sockets[1].sent_text[0])
    blob = json.dumps(second_config, ensure_ascii=False)
    assert "Second Speaker" in blob, "the new context never reached Soniox"
    assert "newterm" in blob
    # The old one is gone, not merged.
    assert SECRETS["speaker"] not in blob
    assert loaded_session.details_pending is False


async def test_apply_now_is_not_a_drop(
    loaded_session, recordings_dir, monkeypatch
):
    """A planned reconnect must not look like a failure to anyone.

    No gap notice on the page, no reconnect counted, `live` untouched, and no
    audio reported dropped - the ring buffer covers the join.
    """
    from test_soniox_client import FakeServer

    monkeypatch.setattr(settings, "soniox_api_key", "test-key")
    server_double = FakeServer()
    source = FileSource(recordings_dir / "tone.wav", realtime=True)
    await loaded_session.start_live(source, connect=server_double,
                                    api_key="test-key")
    await asyncio.sleep(0.05)

    await admin_save_session(token=TOKEN, details=SessionContext(title="x"))
    await admin_apply_session(token=TOKEN)
    for _ in range(300):
        await asyncio.sleep(0.01)
        if server_double.connections >= 2:
            break
    await asyncio.sleep(0.05)
    engine = loaded_session._engine
    reconnects = engine.reconnects
    reconfigures = engine.reconfigures
    await admin_stop(token=TOKEN)

    assert reconfigures >= 1, "the reconnect was not recorded as planned"
    assert reconnects == 0, "a planned reconnect was counted as a failure"
    assert loaded_session.dropped_seconds == 0, "a planned reconnect lost audio"
    notices = [p for lang in ("en", "ar")
               for p in loaded_session.state.streams[lang] if p.notice]
    assert not notices, "the audience was told about a planned reconnect"


async def test_apply_now_with_nothing_running_is_harmless(loaded_session):
    body = json.loads((await admin_apply_session(token=TOKEN)).body)
    assert body["applied"] is False
    assert body["pending"] is False


async def test_clearing_the_details_empties_the_file_too(loaded_session,
                                                         quarantine):
    """The "Clear session details" button, which is a save of nothing."""
    from app.settings import load_session

    await admin_save_session(token=TOKEN, details=SessionContext())
    written = quarantine / "config" / "session.yaml"
    assert load_session(written).is_empty()
    assert loaded_session.details.is_empty()


async def test_the_session_routes_reject_a_bad_token(loaded_session):
    from fastapi import HTTPException

    for call in (
        admin_save_session(token="wrong", details=SessionContext()),
        admin_apply_session(token="wrong"),
    ):
        with pytest.raises(HTTPException) as raised:
            await call
        assert raised.value.status_code == 401
