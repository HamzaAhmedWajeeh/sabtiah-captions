"""FastAPI app: serves the caption pages and (from Phase 3) the viewer socket.

Phase 0 scope: static pages and a health check.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.settings import WEB_DIR

app = FastAPI(title="Sabtiah Live Captions", docs_url=None, redoc_url=None)

# Serve JS/CSS/fonts from web/ without exposing the project root.
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/healthz")
async def healthz() -> JSONResponse:
    return JSONResponse({"ok": True})


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")
