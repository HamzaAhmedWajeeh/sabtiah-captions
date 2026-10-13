"""FastAPI app: the caption pages, the viewer socket, and the session runner.

One process, one event loop (BUILD_SPEC section 6). Three things live here:

- **Routes.** `/` audience, `/screen` big screen, `/admin` operator, `/ws`
  viewers, `/healthz`, and a deliberately narrow `/static/{name}`.
- **`CaptionSession`.** Owns a `CaptionState`, a `BroadcastHub`, and whatever
  is currently feeding them. Two feeds exist so far, and both are free:
  `_run_replay` plays a saved capture through `CaptionState`, and `_run_audio`
  (Phase 4) captures live audio and measures it without transcribing it.
  Phase 5 joins the two by putting the real Soniox client between them, as a
  third `_run_*` coroutine behind the same `start`/`stop`/`status`.
- **Lifespan.** Starts and stops the broadcast loop.

Phases 3 and 4 spend no Soniox credit, by construction: nothing in this
module, and nothing in `audio_source`, can open a network connection outward.
That is CLAUDE.md's budget rule made structural rather than remembered.

Every run is appended to `logs/usage.jsonl` when it ends, free runs included,
so the admin page can show total spend against the $10 budget.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, WebSocket
from fastapi.responses import FileResponse, JSONResponse

from app import usage
from app.audio_source import AudioSource, FileSource, MicSource, describe_devices
from app.soniox_client import (
    PAUSED,
    RECONNECTING,
    STREAMING,
    SonioxClient,
    base_config,
)
from app.broadcast import BroadcastHub
from app.captions import CaptionState
from app.replay import CaptureWriter, FakeSonioxClient
from app.settings import (
    LOG_DIR,
    RECORDING_DIR,
    ROOT,
    WEB_DIR,
    SessionContext,
    build_context,
    load_glossary,
    load_session,
    save_session,
    settings,
)

#: Where a replayable capture may come from. A request names a capture by
#: bare filename and it is looked up here, so no request can reach a path of
#: its own choosing - `recordings/` and `logs/` are not servable or readable
#: through any route (BUILD_SPEC section 8).
CAPTURE_DIRS = (ROOT / "tests" / "fixtures", ROOT / "docs" / "captures")

#: Audio a `FileSource` run may read, by bare filename only, with the same
#: reasoning as `CAPTURE_DIRS`. Reading a recording locally is not serving it:
#: no route returns audio, and `recordings/` stays out of `ASSETS`.
AUDIO_SUFFIXES = (".wav", ".m4a", ".mp3", ".aac", ".flac", ".ogg", ".opus",
                  ".webm", ".mp4", ".wma")

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

#: After the engine has flushed, how long to let the caption task drain the
#: responses that flush produced. Short: the tokens are already in the queue.
FINISH_DRAIN_SECONDS = 1.0


class CaptionSession:
    """The running talk: caption state, viewer hub, and the current feed."""

    def __init__(self) -> None:
        self.state = CaptionState()
        self.hub = BroadcastHub(self.state)
        self.source: str | None = None
        self.mode = "replay"
        self.engine = "none"
        self.paced = True
        self.started_at: float | None = None
        self.responses = 0
        self.dropped_seconds = 0.0
        self.error: str | None = None
        self._task: asyncio.Task | None = None
        self._client: FakeSonioxClient | None = None
        self._audio: AudioSource | None = None
        self._engine: SonioxClient | None = None
        self._capture: CaptureWriter | None = None
        #: This talk's private details, held in memory so an edit applies
        #: without a restart. Loaded from the gitignored config/session.yaml,
        #: which may not exist - a fresh clone has none and that is normal.
        self.details = load_session()
        #: True once the details have changed under a running engine, until
        #: `apply_details` reconnects. The operator needs to know that what
        #: they typed is saved but not yet in effect.
        self.details_pending = False
        self._logged = True

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def level(self) -> dict[str, Any] | None:
        """Input level, or None when nothing is capturing audio."""
        return self._audio.level.as_dict() if self._audio is not None else None

    def _begin(self, *, mode: str, engine: str, source: str) -> None:
        """Reset everything a new run must not inherit from the last one."""
        self.state = CaptionState()
        self.hub.rebind(self.state)
        self.mode = mode
        self.engine = engine
        self.source = source
        self.started_at = time.time()
        self.responses = 0
        self.dropped_seconds = 0.0
        self.error = None
        self._logged = False
        self.state.set_live(True)
        self.hub.notify()

    async def start_replay(self, capture: Path, *, paced: bool = True) -> None:
        """Drive the pipeline from a saved capture. Costs no Soniox credit."""
        await self.stop()
        self.paced = paced
        self._audio = None
        self._engine = None
        self._client = FakeSonioxClient(capture, paced=paced)
        self._begin(mode="replay", engine="replay", source=capture.name)
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
            self._finish(client.sent_ms / 1000)

    async def start_audio(self, source: AudioSource) -> None:
        """Capture live audio and measure it. **Produces no captions.**

        Phase 4 has the microphone but not the engine, so this run exists to
        answer the operator's questions - is the right input selected, is the
        gain right, is anything clipping - and to prove the capture path end to
        end. Phase 5 turns the same source into captions by putting the Soniox
        client between this coroutine and `CaptionState`; nothing here changes.
        """
        await self.stop()
        # Opened here so a missing or busy device raises before the session
        # claims to be running.
        await source.open()
        self._client = None
        self._engine = None
        self._audio = source
        self.paced = getattr(source, "realtime", True)
        self._begin(mode=source.kind, engine="monitor",
                    source=source.describe())
        self._task = asyncio.create_task(self._run_audio(source), name="audio")

    async def _run_audio(self, source: AudioSource) -> None:
        try:
            async for _chunk in source:
                # Phase 5 sends the chunk to Soniox here. Until then the audio
                # is counted and levelled by the source itself and discarded:
                # holding it would be a memory leak with no reader.
                self.responses += 1
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
        finally:
            seconds = source.audio_seconds
            await source.close()
            self._finish(seconds)

    async def start_live(self, source: AudioSource, *, capture: bool = False,
                         **client_kwargs) -> None:
        """The real thing: microphone -> Soniox -> captions -> phones.

        **This spends money.** Everything else in this class is free; this is
        the only path that opens a connection to Soniox, and the usage line it
        writes at the end is the billable one.
        """
        await self.stop()
        if not settings.soniox_api_key:
            raise HTTPException(
                status_code=400,
                detail="SONIOX_API_KEY is not set; live transcription needs it",
            )
        await source.open()
        self._audio = source
        self._client = None
        self.paced = True
        client = SonioxClient(
            source,
            # Built from the details held in memory, not re-read from disk:
            # the operator may have edited them seconds ago.
            context=self.context(),
            on_state=self._on_engine_state,
            on_drop=self._on_engine_drop,
            on_audio_dropped=self._on_audio_dropped,
            **client_kwargs,
        )
        self._engine = client
        self._capture = None
        if capture:
            # Opt-in: a capture is a verbatim transcript of the room. It is
            # also the only way to answer a token-level question after the
            # fact, and it produces a file that replays as a test fixture.
            stamp = time.strftime("%Y-%m-%d-%H%M%S")
            # The config *without* its context. A capture is kept for months
            # and may be committed as a fixture, and the context is where the
            # speaker names live - writing it here would put them in a log
            # file, which is exactly what the session details are kept out of.
            # The shape is recorded instead, which is what a reader of the
            # capture actually needs to know.
            safe_config = {
                key: value for key, value in client._config.items()
                if key != "context"
            }
            safe_config["context"] = self.details.summary()
            self._capture = CaptureWriter(
                LOG_DIR / f"live-{stamp}.jsonl",
                meta={"source": source.describe(), "config": safe_config},
            )
        self._begin(mode=source.kind, engine="soniox", source=source.describe())
        self.state.set_status("connecting")
        self._task = asyncio.create_task(self._run_live(client), name="live")

    def context(self) -> dict:
        """The Soniox `context` for the next connection.

        The public glossary is re-read each time, because it is a file anyone
        may edit; the private details come from memory, because `/admin` owns
        them.
        """
        return build_context(load_glossary(), self.details)

    def save_details(self, details: SessionContext) -> bool:
        """Persist and adopt new session details. True if a reconnect is
        needed to apply them."""
        save_session(details)
        self.details = details
        running = self._engine is not None and self.running
        self.details_pending = running
        return running

    def apply_details(self) -> bool:
        """Reconnect the engine so the new context takes effect.

        Returns False when nothing is running - then there is nothing to
        apply and the details are already in place for the next Start.
        """
        engine = self._engine
        if engine is None or not self.running:
            self.details_pending = False
            return False
        applied = engine.reconfigure(base_config(self.context()))
        self.details_pending = not applied
        return applied

    def _on_engine_state(self, state: str) -> None:
        """Mirror the engine's state onto the pages.

        `live` deliberately does not change here (BUILD_SPEC Phase 5): a
        reconnect must not make the audience page say the talk ended. Only
        `stop()` clears `live`.
        """
        shown = {STREAMING: "live", RECONNECTING: "reconnecting",
                 PAUSED: "paused"}.get(state, state)
        if self.state.set_status(shown):
            self.hub.notify()

    def _on_engine_drop(self) -> None:
        """A connection just died. Mark the discontinuity.

        Two things, both learned from run7 (2026-10-13): an uncommitted
        partial would otherwise sit grey on every phone for the length of the
        reconnect, and the first token of the *new* stream carries no leading
        space, so appending it glued two sentences together
        (`behindMicrosoft`). `mark_gap` handles both.
        """
        if self.state.mark_gap():
            self.hub.notify()

    def _on_audio_dropped(self, seconds: float) -> None:
        """Speech was captured and never sent. Say so on the page.

        The alternative to a replay that keeps the captions permanently late
        (the decision of 2026-10-13) is a visible gap, and a gap nobody is
        told about reads as the speaker changing the subject.
        """
        self.dropped_seconds += seconds
        self.state.add_notice()
        self.hub.notify()

    async def _run_live(self, client: SonioxClient) -> None:
        try:
            async for response in client:
                self.responses += 1
                if self._capture is not None:
                    self._capture.add(response,
                                      sent_ms=round(client.audio_seconds_sent * 1000))
                if self.state.add_response(response):
                    self.hub.notify()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
        finally:
            if self._capture is not None:
                self._capture.close()
            if client.last_error and not self.error:
                self.error = "{}: {}".format(
                    client.last_error.get("error_type"),
                    client.last_error.get("error_message", ""),
                ).strip(": ")
            streamed = client.audio_seconds_sent
            await client.close()
            audio, self._audio = self._audio, None
            if audio is not None:
                await audio.close()
            self.state.set_status("idle")
            self._finish(streamed)

    def _finish(self, audio_seconds: float) -> None:
        """End of run: drop the live flag and write the usage line.

        Called from the feed's `finally`, so it runs whether the source ran
        out, failed, or was cancelled by the operator. `_logged` guards
        against a second line when `stop()` also tidies up.
        """
        self.state.set_live(False)
        self.hub.notify()
        if self._logged:
            return
        self._logged = True
        usage.record_run(
            engine=self.engine,
            source=self.mode,
            audio_seconds=audio_seconds,
            detail=self.source or "",
        )

    async def stop(self) -> None:
        # Ask the engine to flush before anything is cancelled. Soniox holds
        # an open segment until it is told the audio ended, so cutting the
        # socket here loses the last words of the talk - run9 stopped at
        # "this is Hamza sig". Bounded, so a dead link cannot hang Stop.
        engine = self._engine
        if engine is not None and self.running:
            await engine.finish()
            if self._task is not None and not self._task.done():
                # ...and give `_run_live` a moment to fold the tokens that
                # flush produced into the captions before they are dropped.
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(
                        asyncio.shield(self._task), FINISH_DRAIN_SECONDS
                    )

        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.wait({task})
        engine, self._engine = self._engine, None
        if engine is not None:
            await engine.close()
        audio, self._audio = self._audio, None
        if self._engine is not None:
            status.update(self._engine.status())
        if audio is not None:
            await audio.close()
        self.state.set_live(False)
        self.state.set_status("idle")
        self.hub.notify()

    async def shutdown(self) -> None:
        await self.stop()
        await self.hub.stop()

    def status(self) -> dict[str, Any]:
        """Everything `/admin` shows. No API key, no caption text."""
        client = self._client
        audio = self._audio
        status: dict[str, Any] = {
            "running": self.running,
            "mode": self.mode,
            # `self.engine` is deliberately absent: it names the engine, and
            # both the served static files and this payload are checked
            # against that name (BUILD_SPEC section 8). `engine_state` below
            # carries everything the operator actually needs.
            "source": self.source,
            "paced": self.paced,
            "responses": self.responses,
            # From the session, not the engine, so it survives the engine
            # being torn down at Stop - the operator wants to know what was
            # lost *after* the talk, not only during it.
            "dropped_seconds": round(self.dropped_seconds, 1),
            "capture": str(self._capture.path) if self._capture else None,
            # Counts, never values. The full details go out only from
            # `/admin/session`, which is a separate request behind the same
            # token - so nothing that merely polls status ever carries a
            # speaker's name.
            "details": self.details.summary(),
            "details_pending": self.details_pending,
            "error": self.error,
            "elapsed": (
                round(time.time() - self.started_at, 1)
                if self.started_at else None
            ),
            "audio_ms": client.sent_ms if client else 0,
            "audio_total_s": round(client.audio_seconds, 1) if client else 0,
            "captures": available_captures(),
            "audio_files": available_audio(),
            "usage": usage.totals().as_dict(),
            **self.hub.status(),
        }
        if self._engine is not None:
            status.update(self._engine.status())
        if self._engine is not None:
            status.update(self._engine.status())
        if audio is not None:
            # A live source overrides `audio_ms`: for mic and file runs it is
            # audio actually captured, not a position in a recording. Only a
            # file knows its length, so `audio_total_s` stays absent for a mic.
            source_status = audio.status()
            total_ms = source_status.get("total_ms")
            status.update(source_status)
            status["audio_total_s"] = (
                round(total_ms / 1000, 1) if total_ms else 0
            )
        return status


session = CaptionSession()


#: What to say when there is no admin token, written out here because it is
#: the first thing an operator sees when the server will not come up.
NO_TOKEN_MESSAGE = (
    "ADMIN_TOKEN is not set, so refusing to start.\n"
    "\n"
    "  The operator page would be unreachable and the session unstartable,\n"
    "  which is a worse failure at a venue than this one: it looks like the\n"
    "  server is working.\n"
    "\n"
    "  Put a long random value in {env}:\n"
    "\n"
    "      ADMIN_TOKEN=...\n"
    "\n"
    "  Generate one with:  [Guid]::NewGuid().ToString('N')   (PowerShell)\n"
    "                      openssl rand -hex 16              (macOS)\n"
    "\n"
    "  That file is read from the repository root whatever directory the\n"
    "  server is started from, and it is the only place the token belongs -\n"
    "  do not pass one on the command line."
)


def require_admin_token_configured() -> None:
    """Refuse to start without an admin token.

    `require_admin` already denies every request when the token is unset,
    which is the right call at request time but makes for a confusing venue:
    the pages serve, the audience sees "not live", and `/admin` returns 401
    no matter what is typed. Failing at startup says why, once, in the
    terminal the operator is already looking at.
    """
    if not settings.admin_token.strip():
        raise RuntimeError(NO_TOKEN_MESSAGE.format(env=ROOT / ".env"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    require_admin_token_configured()
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


def available_audio() -> list[str]:
    """Recordings a `FileSource` run may be pointed at, newest first.

    Ordered by modification time because the useful one is almost always the
    clip just recorded, and `recordings/` accumulates.
    """
    if not RECORDING_DIR.is_dir():
        return []
    files = [
        p for p in RECORDING_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in AUDIO_SUFFIXES
    ]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [p.name for p in files]


def resolve_audio(name: str) -> Path:
    """Look an audio file up inside `recordings/`, by filename only.

    Same containment as `resolve_capture`: only the final path component is
    used, so no request can name a file elsewhere on the laptop.
    """
    wanted = Path(name or "").name
    if Path(wanted).suffix.lower() not in AUDIO_SUFFIXES:
        raise HTTPException(status_code=400, detail="not an audio file")
    candidate = RECORDING_DIR / wanted
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="no such recording")
    return candidate


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


@app.get("/admin/session")
async def admin_session(token: str | None = Query(default=None)) -> JSONResponse:
    """This talk's details, in full.

    **The only route that returns these values**, and it is behind the admin
    token. Everything else - the audience pages, the viewer socket, the status
    poll, every log - carries counts at most.
    """
    require_admin(token)
    return JSONResponse(
        {
            "details": session.details.model_dump(),
            "pending": session.details_pending,
            "running": session.running,
            # So the operator can see what the glossary already covers and
            # not retype it. Public vocabulary, safe to show.
            "glossary_terms": load_glossary().terms,
        },
        headers=NO_STORE,
    )


@app.post("/admin/session")
async def admin_save_session(
    token: str | None = Query(default=None),
    details: SessionContext | None = None,
) -> JSONResponse:
    """Save this talk's details. Written atomically, adopted immediately.

    Applied to a *running* engine only on request - see `/admin/session/apply`.
    Reconnecting without being asked would be a surprise in the middle of a
    talk, and the operator may want to type several fields before any of it
    takes effect.
    """
    require_admin(token)
    needs_apply = session.save_details(details or SessionContext())
    return JSONResponse(
        {"saved": True, "pending": needs_apply,
         "details": session.details.model_dump()},
        headers=NO_STORE,
    )


@app.post("/admin/session/apply")
async def admin_apply_session(
    token: str | None = Query(default=None),
) -> JSONResponse:
    """Reconnect the engine so saved details take effect mid-talk.

    Uses the rollover path: the current stream ends gracefully, a new one
    opens with the new context, and the ring buffer covers the join. No audio
    is dropped and the audience sees no notice, because nothing went wrong.
    """
    require_admin(token)
    applied = session.apply_details()
    return JSONResponse(
        {"applied": applied, "pending": session.details_pending},
        headers=NO_STORE,
    )


@app.get("/admin/level")
async def admin_level(token: str | None = Query(default=None)) -> JSONResponse:
    """Just the input level.

    The meter wants several updates a second - a level that moves once a
    second cannot show a syllable - and `/admin/status` is too expensive to
    ask that often: it stats the capture directories and re-reads the usage
    ledger. This returns two numbers and touches no disk.
    """
    require_admin(token)
    return JSONResponse(
        {"running": session.running, "level": session.level},
        headers=NO_STORE,
    )


@app.get("/admin/devices")
async def admin_devices(token: str | None = Query(default=None)) -> JSONResponse:
    """Input devices for the operator's dropdown.

    Its own endpoint rather than part of `/admin/status`: querying PortAudio
    takes tens of milliseconds and the device list does not change between
    polls, so doing it once a second would be a waste that grows with every
    page left open.
    """
    require_admin(token)
    return JSONResponse({"devices": describe_devices()}, headers=NO_STORE)


@app.post("/admin/start")
async def admin_start(
    token: str | None = Query(default=None),
    # Plain defaults rather than `Query(default=...)`: `tests/test_server.py`
    # calls these handlers as the coroutines they are, and an unresolved
    # `Query` object would arrive as the argument's value. FastAPI treats a
    # scalar with a plain default as a query parameter either way.
    source: str = "replay",
    capture: str | None = None,
    file: str | None = None,
    device: int | None = None,
    channel: int = 1,
    record: bool = False,
    # Not `capture`: that already names the replay file to play back. This
    # one records the engine's raw responses from a live run.
    log_responses: bool = False,
    paced: bool = True,
) -> JSONResponse:
    """Start a session.

    `source=replay` plays a saved capture through `CaptionState` - captions,
    no audio. `source=mic` and `source=file` capture audio and measure it -
    audio, no captions, because the engine arrives in Phase 5. Neither one
    can spend Soniox credit.
    """
    require_admin(token)
    if source == "replay":
        if not capture:
            raise HTTPException(status_code=400, detail="capture required")
        await session.start_replay(resolve_capture(capture), paced=paced)
    elif source == "mic":
        try:
            await session.start_audio(
                MicSource(device, channel=channel, record=record)
            )
        except Exception as exc:
            # A device that cannot be opened is the operator's problem to
            # fix, not a server fault: say which device and why, and leave
            # the session stopped rather than half-started.
            raise HTTPException(
                status_code=400,
                detail=f"could not open device {device}: {exc}",
            ) from exc
    elif source == "file":
        if not file:
            raise HTTPException(status_code=400, detail="file required")
        await session.start_audio(
            FileSource(resolve_audio(file), realtime=paced,
                       channel=channel, record=record)
        )
    elif source == "live":
        # The only route in this file that spends money. One shape, one
        # source: the microphone. Streaming a saved file to the engine is
        # what `tools/probe_soniox.py` is for, and keeping it out of here
        # means there is exactly one way to start billing.
        try:
            mic = MicSource(device, channel=channel, record=record)
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail=f"could not open device {device}: {exc}",
            ) from exc
        try:
            await session.start_live(mic, capture=log_responses)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail=f"could not start live transcription: {exc}",
            ) from exc
    else:
        raise HTTPException(status_code=400, detail="source must be "
                                                   "replay, mic, file or live")
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
