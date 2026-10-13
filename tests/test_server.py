"""Phase 3: routes, the admin gate, and the replay session. Offline.

Route handlers are called as the coroutines they are, rather than through
`fastapi.testclient`: `TestClient` needs `httpx`, which is not in BUILD_SPEC
section 2's dependency list, and section 9 says to ask before adding one. The
handlers are thin enough that calling them directly tests the same logic -
and the socket endpoint is better tested with a fake WebSocket anyway, since
that is the only way to feed it the messages a hostile client would send.

The security checks here are BUILD_SPEC section 8 in executable form: the key
never reaches a served file, the admin page is unreachable without its token,
an unconfigured token denies rather than allows, and no request can name a
file outside `CAPTURE_DIRS`.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import main as server  # noqa: E402
from app import usage  # noqa: E402
from app.main import (  # noqa: E402
    ASSETS,
    CaptionSession,
    admin,
    admin_start,
    admin_status,
    admin_stop,
    asset,
    available_captures,
    healthz,
    index,
    require_admin,
    resolve_capture,
    screen,
    viewer_socket,
)
from app.settings import WEB_DIR, settings  # noqa: E402

TOKEN = "s3cret-operator-token"

#: Every file the server will hand to a browser: the allowlisted assets plus
#: the three pages. Anything added here must stay free of credentials.
SERVED = sorted(set(ASSETS) | {"index.html", "screen.html", "admin.html"})


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


class FakeWebSocket:
    """Starlette's WebSocket, reduced to what the endpoint touches.

    `receive` returns raw ASGI event dicts, which is what lets a test send a
    binary frame or an oversized one - the two shapes a read-only endpoint
    has to shrug off rather than crash on.
    """

    def __init__(self, incoming: list[dict] | None = None) -> None:
        self.incoming = list(incoming or [])
        self.accepted = False
        self.sent: list[str] = []
        self.closed: int | None = None

    async def accept(self) -> None:
        self.accepted = True

    async def receive(self) -> dict:
        if self.incoming:
            return self.incoming.pop(0)
        return {"type": "websocket.disconnect", "code": 1000}

    async def send_text(self, text: str) -> None:
        self.sent.append(text)

    async def close(self, code: int = 1000) -> None:
        self.closed = code


@pytest.fixture(autouse=True)
def quarantine_usage_log(tmp_path, monkeypatch):
    """Point the spend ledger at a tmp directory for every test here.

    Phase 4 made every run append to `logs/usage.jsonl`, which means a test
    suite that starts a few dozen replays writes a few dozen lines into the
    operator's real ledger. The ledger is the only record of what the $10 was
    spent on; a run nobody performed must never appear on it.
    """
    monkeypatch.setattr(usage, "LOG_DIR", tmp_path / "logs")


@pytest.fixture(autouse=True)
async def fresh_session(monkeypatch):
    """A clean `CaptionSession` per test, never the imported module global."""
    made = CaptionSession()
    monkeypatch.setattr(server, "session", made)
    yield made
    await made.shutdown()


@pytest.fixture
def admin_token(monkeypatch):
    monkeypatch.setattr(settings, "admin_token", TOKEN)
    return TOKEN


async def drain(session, limit: int = 20_000) -> None:
    """Let an unpaced replay run to completion."""
    for _ in range(limit):
        if not session.running:
            return
        await asyncio.sleep(0)
    raise AssertionError("replay did not finish")


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


async def test_healthz_is_ok():
    response = await healthz()
    assert json.loads(response.body) == {"ok": True}


@pytest.mark.parametrize(
    "handler,filename",
    [(index, "index.html"), (screen, "screen.html")],
)
async def test_public_pages_are_served(handler, filename):
    response = await handler()
    assert Path(response.path).name == filename
    # A phone that reloads after a mid-talk fix must not get a cached page.
    assert response.headers["cache-control"] == "no-store"


async def test_every_served_page_exists():
    for name in SERVED:
        assert (WEB_DIR / name).is_file(), f"{name} is routed but missing"


# ---------------------------------------------------------------------------
# The admin gate (BUILD_SPEC section 8)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("supplied", [None, "", "wrong", TOKEN + "x", TOKEN[:-1]])
async def test_admin_refuses_a_bad_token(admin_token, supplied):
    with pytest.raises(HTTPException) as raised:
        require_admin(supplied)
    assert raised.value.status_code == 401


async def test_admin_accepts_the_configured_token(admin_token):
    require_admin(TOKEN)
    response = await admin(token=TOKEN)
    assert Path(response.path).name == "admin.html"


async def test_an_unset_admin_token_denies_rather_than_allows(monkeypatch):
    """A fresh clone with no `.env` must not expose the operator page."""
    monkeypatch.setattr(settings, "admin_token", "")
    for supplied in (None, "", "anything"):
        with pytest.raises(HTTPException):
            require_admin(supplied)


async def test_a_non_ascii_token_is_rejected_not_a_crash(admin_token):
    """`compare_digest` raises on non-ASCII `str`; the token is attacker text."""
    with pytest.raises(HTTPException):
        require_admin("رمز-خاطئ")


@pytest.mark.parametrize("handler", [admin_status, admin_stop])
async def test_admin_endpoints_each_recheck_the_token(admin_token, handler):
    with pytest.raises(HTTPException) as raised:
        await handler(token="wrong")
    assert raised.value.status_code == 401


async def test_admin_start_rechecks_the_token(admin_token):
    with pytest.raises(HTTPException) as raised:
        await admin_start(token=None, capture="run1.jsonl")
    assert raised.value.status_code == 401


# ---------------------------------------------------------------------------
# Static assets
# ---------------------------------------------------------------------------


async def test_static_serves_the_allowlist():
    for name, media_type in ASSETS.items():
        response = await asset(name)
        assert Path(response.path).name == name
        assert response.media_type == media_type


@pytest.mark.parametrize(
    "name",
    [
        "admin.html",      # a StaticFiles mount on web/ would hand this out
        "index.html",
        "../.env",
        "../app/main.py",
        "..\\.env",
        "style.css.map",
        "",
    ],
)
async def test_static_refuses_everything_else(name):
    with pytest.raises(HTTPException) as raised:
        await asset(name)
    assert raised.value.status_code == 404


def test_served_files_carry_no_credentials():
    """BUILD_SPEC section 8: "grep served static files for SONIOX"."""
    for name in SERVED:
        text = (WEB_DIR / name).read_text(encoding="utf-8")
        upper = text.upper()
        assert "SONIOX" not in upper, f"{name} mentions Soniox"
        assert "ADMIN_TOKEN" not in upper, f"{name} mentions the admin token"
        assert "BEARER" not in upper, f"{name} carries an auth header"


def test_the_audience_pages_never_link_to_admin():
    """Section 8: `/admin` is "never linked from the audience page"."""
    for name in ("index.html", "app.js", "screen.html", "screen.js", "style.css"):
        text = (WEB_DIR / name).read_text(encoding="utf-8")
        assert "/admin" not in text, f"{name} points at the operator page"


def test_the_env_example_is_the_only_place_a_key_is_named():
    """The repo must not have gained a committed key while Phase 3 landed."""
    for path in (ROOT / "app").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "sk-" not in text
        assert "Bearer " not in text


# ---------------------------------------------------------------------------
# Capture lookup
# ---------------------------------------------------------------------------


def test_available_captures_lists_the_fixtures():
    names = available_captures()
    assert "run1.jsonl" in names
    assert names == sorted(set(names))


def test_a_capture_is_found_by_bare_name():
    assert resolve_capture("run1.jsonl").is_file()


@pytest.mark.parametrize(
    "name",
    [
        "../../.env",
        "..\\..\\.env",
        "/etc/passwd",
        "C:\\Windows\\win.ini",
        "../app/main.py",
        ".env",
        "",
        "run1",
        "recordings/english-2min.m4a",
    ],
)
def test_capture_lookup_refuses_anything_else(name):
    """Only the final path component is used, so traversal cannot reach out."""
    with pytest.raises(HTTPException) as raised:
        resolve_capture(name)
    assert raised.value.status_code in (400, 404)


def test_a_capture_path_collapses_to_its_filename():
    """A full path to a real capture still resolves - via its name, not itself."""
    resolved = resolve_capture("docs/captures/run1.jsonl")
    assert resolved.name == "run1.jsonl"
    assert resolved.is_file()


# ---------------------------------------------------------------------------
# The replay session
# ---------------------------------------------------------------------------


async def test_a_replay_drives_captions_and_ends_not_live(fresh_session, admin_token):
    await admin_start(token=TOKEN, capture="run5-english.jsonl", paced=False)
    assert fresh_session.state.live is True

    await drain(fresh_session)

    assert fresh_session.responses > 0
    assert fresh_session.error is None
    assert fresh_session.state.render("ar").strip()
    assert fresh_session.state.render("en").strip()
    # The capture ran out: the pages must stop claiming a live talk.
    assert fresh_session.state.live is False


async def test_a_viewer_sees_the_replay(fresh_session, admin_token):
    socket = FakeWebSocket()
    viewer = await fresh_session.hub.add(socket)
    assert viewer is not None

    await admin_start(token=TOKEN, capture="run5-english.jsonl", paced=False)
    await drain(fresh_session)
    await fresh_session.hub.flush()
    for _ in range(6):
        await asyncio.sleep(0)

    messages = [json.loads(text) for text in socket.sent]
    assert messages[0]["type"] == "snapshot"
    assert any(m["type"] in ("snapshot", "diff") and m["streams"]["en"]
               for m in messages[1:])


async def test_stop_halts_a_running_replay(fresh_session, admin_token):
    await admin_start(token=TOKEN, capture="run1.jsonl", paced=True)
    assert fresh_session.running

    await admin_stop(token=TOKEN)

    assert not fresh_session.running
    assert fresh_session.state.live is False


async def test_starting_again_replaces_the_session(fresh_session, admin_token):
    await admin_start(token=TOKEN, capture="run5-english.jsonl", paced=False)
    await drain(fresh_session)
    first = fresh_session.state.render("en")
    assert first.strip()

    await admin_start(token=TOKEN, capture="run5-english.jsonl", paced=False)
    await drain(fresh_session)

    # A fresh state, not the previous talk with a second copy appended.
    assert fresh_session.state.render("en") == first


async def test_status_is_safe_to_serve(fresh_session, admin_token):
    await admin_start(token=TOKEN, capture="run5-english.jsonl", paced=False)
    await drain(fresh_session)

    response = await admin_status(token=TOKEN)
    body = json.loads(response.body)

    assert body["source"] == "run5-english.jsonl"
    assert body["responses"] > 0
    assert body["viewers"] == 0
    assert "run1.jsonl" in body["captures"]
    # No key, and no caption text: this is a status page, not a transcript.
    assert "soniox" not in json.dumps(body).lower()


async def test_an_unknown_capture_is_a_404(admin_token):
    with pytest.raises(HTTPException) as raised:
        await admin_start(token=TOKEN, capture="no-such-run.jsonl")
    assert raised.value.status_code == 404


# ---------------------------------------------------------------------------
# The viewer socket
# ---------------------------------------------------------------------------


async def test_a_socket_is_accepted_and_sent_a_snapshot(fresh_session):
    socket = FakeWebSocket()

    await viewer_socket(socket)

    assert socket.accepted
    assert json.loads(socket.sent[0])["type"] == "snapshot"
    assert fresh_session.hub.viewer_count == 0  # disconnected, then cleaned up


async def test_only_a_resync_is_acted_on(fresh_session, monkeypatch):
    """BUILD_SPEC: "ignore any client message except `{"type":"resync"}`"."""
    seen: list[int] = []
    monkeypatch.setattr(
        fresh_session.hub, "resync", lambda viewer: seen.append(viewer.ident)
    )
    socket = FakeWebSocket([
        {"type": "websocket.receive", "text": '{"type":"resync"}'},
        {"type": "websocket.receive", "text": '{"type":"clear"}'},
        {"type": "websocket.receive", "text": '{"type":"resync","extra":1}'},
        {"type": "websocket.receive", "text": "not json at all"},
        {"type": "websocket.receive", "text": '"resync"'},
        {"type": "websocket.receive", "text": '["resync"]'},
        {"type": "websocket.receive", "bytes": b"\x00\x01audio?"},
        {"type": "websocket.receive", "text": '{"type":"resync"}' + " " * 500},
        {"type": "websocket.disconnect", "code": 1000},
    ])

    await viewer_socket(socket)

    # Two honoured: the plain one and the one with a harmless extra field.
    # The oversized copy is ignored on length alone, before parsing.
    assert len(seen) == 2


async def test_a_full_house_refuses_a_socket_without_serving_it(
    fresh_session, monkeypatch
):
    monkeypatch.setattr(fresh_session.hub, "max_viewers", 0)
    socket = FakeWebSocket()

    await viewer_socket(socket)

    assert socket.accepted
    assert socket.sent == []
    assert socket.closed == 1013


async def test_a_viewer_is_removed_when_its_page_closes(fresh_session):
    socket = FakeWebSocket([{"type": "websocket.disconnect", "code": 1001}])

    await viewer_socket(socket)

    assert fresh_session.hub.viewer_count == 0
