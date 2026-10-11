"""FastAPI app: the caption pages, the viewer socket, and the session runner.

One process, one event loop (BUILD_SPEC section 6). Three things live here:

- **Routes.** `/` audience, `/screen` big screen, `/admin` operator, `/ws`
  viewers, `/healthz`, and a deliberately narrow `/static/{name}`.
- **`CaptionSession`.** Owns a `CaptionState`, a `BroadcastHub`, and whatever
  is currently feeding them. In Phase 3 the only feed is a replayed capture
  (`replay.FakeSonioxClient`); Phase 4 adds the microphone and Phase 5 the
  real Soniox client, both arriving as a different `_run_*` coroutine behind
  the same `start`/`stop`/`status`.
- **Lifespan.** Starts and stops the broadcast loop.

Phase 3 spends no Soniox credit, by construction: nothing in this module can
open a network connection outward. That is CLAUDE.md's budget rule made
structural rather than remembered.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, WebSocket
from fastapi.responses import FileResponse, JSONResponse

from app.broadcast import BroadcastHub
from app.captions import CaptionState
from app.replay import FakeSonioxClient
from app.settings import ROOT, WEB_DIR, settings

#: Where a replayable capture may come from. A request names a capture by
#: bare filename and it is looked up here, so no request can reach a path of
#: its own choosing - `recordings/` and `logs/` are not servable or readable
#: through any route (BUILD_SPEC section 8).
CAPTURE_DIRS = (ROOT / "tests" / "fixtures", ROOT / "docs" / "captures")

#: Exactly what `/static` will serve, with its content type. An allowlist
#: rather than a `StaticFiles` mount on `web/`: a mount would also serve
#: `admin.html` to anyone who guessed the path, and would serve any stray
#: file that ever lands in that directory. Keeping the list explicit is also
#: what makes the Phase 8 "grep served static files for SONIOX" test exact.
ASSETS = {
    "style.css": "text/css; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
    "screen.js": "text/javascript; charset=utf-8",
    "admin.js": "text/javascript; charset=utf-8",
}

#: Pages are never cached. A phone that reloads after a mid-talk fix must get
#: the fixed page, not yesterday's from its HTTP cache.
NO_STORE = {"Cache-Control": "no-store"}

#: Nothing a read-only client legitimately sends is larger than this.
MAX_CLIENT_MESSAGE = 200


class CaptionSession:
    """The running talk: caption state, viewer hub, and the current feed."""

    def __init__(self) -> None:
        self.state = CaptionState()
        self.hub = BroadcastHub(self.state)
        self.source: str | None = None
        self.paced = True
        self.started_at: float | None = None
        self.responses = 0
        self.error: str | None = None
        self._task: asyncio.Task | None = None
        self._client: FakeSonioxClient | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start_replay(self, capture: Path, *, paced: bool = True) -> None:
        """Drive the pipeline from a saved capture. Costs no Soniox credit."""
        await self.stop()
        self.state = CaptionState()
        self.hub.rebind(self.state)
        self.source = capture.name
        self.paced = paced
        self.started_at = time.time()
        self.responses = 0
        self.error = None
        self._client = FakeSonioxClient(capture, paced=paced)
        self.state.set_live(True)
        self.hub.notify()
        self._task = asyncio.create_task(self._run_replay(self._client),
                                         name="replay")

    async def _run_replay(self, client: FakeSonioxClient) -> None:
        try:
            async for response in client:
                self.responses += 1
                if self.state.add_response(response):
                    # The hub decides when this reaches a phone; at 10 Hz most
                    # of these notifications coalesce into nothing.
                    self.hub.notify()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # a malformed capture, not a live failure
            self.error = f"{type(exc).__name__}: {exc}"
        finally:
            # The capture ran out, or the operator stopped it. Either way the
            # session is no longer live, and the pages must say so.
            self.state.set_live(False)
            self.hub.notify()

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.wait({task})
        self.state.set_live(False)
        self.hub.notify()

    async def shutdown(self) -> None:
        await self.stop()
        await self.hub.stop()

    def status(self) -> dict[str, Any]:
        """Everything `/admin` shows. No API key, no caption text."""
        client = self._client
        return {
            "running": self.running,
            "source": self.source,
            "paced": self.paced,
            "responses": self.responses,
            "error": self.error,
            "elapsed": (
                round(time.time() - self.started_at, 1)
                if self.started_at else None
            ),
            "audio_ms": client.sent_ms if client else 0,
            "audio_total_s": round(client.audio_seconds, 1) if client else 0,
            "captures": available_captures(),
            **self.hub.status(),
        }


session = CaptionSession()


@asynccontextmanager
async def lifespan(app: FastAPI):
    session.hub.start()
    try:
        yield
    finally:
        await session.shutdown()


app = FastAPI(
    title="Sabtiah Live Captions",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)


# -- capture lookup --------------------------------------------------------


def available_captures() -> list[str]:
    """Replayable capture filenames, deduplicated, in a stable order."""
    names = {p.name for d in CAPTURE_DIRS if d.is_dir() for p in d.glob("*.jsonl")}
    return sorted(names)


def resolve_capture(name: str) -> Path:
    """Look a capture up by filename inside `CAPTURE_DIRS`.

    Only the final path component is used, so `../../.env` and an absolute
    path both collapse to a harmless name that simply will not be found.
    """
    wanted = Path(name or "").name
    if not wanted.endswith(".jsonl"):
        raise HTTPException(status_code=400, detail="not a .jsonl capture")
    for directory in CAPTURE_DIRS:
        candidate = directory / wanted
        if candidate.is_file():
            return candidate
    raise HTTPException(status_code=404, detail="no such capture")


# -- admin auth ------------------------------------------------------------


def require_admin(token: str | None) -> None:
    """Constant-time token check (BUILD_SPEC section 8).

    An unset `ADMIN_TOKEN` denies everything rather than allowing everything:
    a fresh clone with no `.env` must not expose the operator page. The
    comparison runs on bytes because `compare_digest` rejects non-ASCII `str`,
    and a token is attacker-supplied text.
    """
    expected = settings.admin_token.encode()
    supplied = (token or "").encode()
    # Compared even when `expected` is empty, so the failure path costs the
    # same either way and reveals nothing about whether a token is configured.
    ok = secrets.compare_digest(supplied, expected) and bool(expected)
    if not ok:
        raise HTTPException(status_code=401, detail="admin token required")


# -- pages -----------------------------------------------------------------


def _page(name: str) -> FileResponse:
    return FileResponse(WEB_DIR / name, media_type="text/html; charset=utf-8",
                        headers=NO_STORE)


@app.get("/healthz")
async def healthz() -> JSONResponse:
    return JSONResponse({"ok": True})


@app.get("/")
async def index() -> FileResponse:
    return _page("index.html")


@app.get("/screen")
async def screen() -> FileResponse:
    """Big-screen view. `?lang=` and `?lines=` are read client-side."""
    return _page("screen.html")


@app.get("/admin")
async def admin(token: str | None = Query(default=None)) -> FileResponse:
    require_admin(token)
    return _page("admin.html")


@app.get("/static/{name}")
async def asset(name: str) -> FileResponse:
    media_type = ASSETS.get(name)
    if media_type is None:
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(WEB_DIR / name, media_type=media_type, headers=NO_STORE)


# -- operator actions ------------------------------------------------------


@app.get("/admin/status")
async def admin_status(token: str | None = Query(default=None)) -> JSONResponse:
    require_admin(token)
    return JSONResponse(session.status(), headers=NO_STORE)


@app.post("/admin/start")
async def admin_start(
    token: str | None = Query(default=None),
    capture: str = Query(...),
    paced: bool = Query(default=True),
) -> JSONResponse:
    """Start a replay session. Phase 4 adds `source=mic` here."""
    require_admin(token)
    path = resolve_capture(capture)
    await session.start_replay(path, paced=paced)
    return JSONResponse(session.status(), headers=NO_STORE)


@app.post("/admin/stop")
async def admin_stop(token: str | None = Query(default=None)) -> JSONResponse:
    require_admin(token)
    await session.stop()
    return JSONResponse(session.status(), headers=NO_STORE)


# -- viewers ---------------------------------------------------------------


@app.websocket("/ws")
async def viewer_socket(websocket: WebSocket) -> None:
    """A page. Receives a snapshot then diffs, and is otherwise read-only."""
    await websocket.accept()
    viewer = await session.hub.add(websocket)
    if viewer is None:  # at capacity, or the socket died on the snapshot
        return
    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            text = message.get("text")
            # Binary frames and oversized text are ignored, not errors: the
            # contract is that the server reads nothing but a resync request.
            if not text or len(text) > MAX_CLIENT_MESSAGE:
                continue
            try:
                payload = json.loads(text)
            except ValueError:
                continue
            if isinstance(payload, dict) and payload.get("type") == "resync":
                session.hub.resync(viewer)
    except Exception:
        # Any receive failure means the page is gone. Nothing to report.
        pass
    finally:
        await session.hub.remove(viewer)
