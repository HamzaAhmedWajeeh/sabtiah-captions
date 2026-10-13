"""Screenshot the operator page in each state it matters in.

    uv run --with playwright python tools/admin_screenshots.py

Drives the **real** page against the **real** server, with only the server's
JSON answers faked by intercepting the four `/admin/*` requests the page
makes. So the HTML, the stylesheet and admin.js are exactly what ships, and
no Soniox call is made - the live and error states are states the server can
reach, not states it is put into here.

Playwright is deliberately **not** a project dependency: BUILD_SPEC section 2
lists what this project installs, and a browser engine is not on it. Run it
with `uv run --with playwright`, which keeps it out of `pyproject.toml`.

Every value shown is invented. Real speaker names never appear in a file that
gets committed.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT = ROOT / "docs" / "screens"
BASE = "http://127.0.0.1:8000"

SIZES = {"desktop": (1920, 1080), "phone": (390, 844)}

#: Invented, and obviously so. Never a real speaker.
DUMMY_DETAILS = {
    "title": "Building with agents",
    "speakers": ["Alex Rivera", "ليلى منصور"],
    "companies": ["Northwind Labs"],
    "terms": ["retrieval", "guardrails"],
    "notes": "Demo in the second half.",
}

DEVICES = [
    {"index": 0, "name": "Sound Mapper - Input", "api": "MME",
     "channels": 2, "sample_rate": 44100, "default": True},
    {"index": 17, "name": "Headset Microphone", "api": "Windows WASAPI",
     "channels": 4, "sample_rate": 48000, "default": False},
]

CAPTURES = ["run1.jsonl", "run2-oneway.jsonl", "run4-saudi.jsonl",
            "run5-english.jsonl"]

BASE_STATUS = {
    "running": False,
    "mode": "replay",
    "source": None,
    "paced": True,
    "responses": 0,
    "dropped_seconds": 0.0,
    "capture": None,
    "details": {"title": True, "speakers": 2, "companies": 1, "terms": 2,
                "notes_chars": 24},
    "details_pending": False,
    "error": None,
    "elapsed": None,
    "audio_ms": 0,
    "audio_total_s": 0,
    "captures": CAPTURES,
    "audio_files": ["tone.wav"],
    "usage": {
        "runs": 8, "billable_runs": 7, "billable_minutes": 27.7,
        "free_minutes": 19.1, "estimated_usd": None, "budget_usd": 10.0,
        "budget_remaining_usd": None, "rate_per_minute": None,
        "unreadable_lines": 0,
    },
    "viewers": 0, "max_viewers": 500, "rejected": 0, "dropped": 0,
    "seq": 0, "live": False,
}


def merged(**extra) -> dict:
    status = dict(BASE_STATUS)
    usage = dict(status["usage"])
    usage.update(extra.pop("usage", {}))
    status.update(extra)
    status["usage"] = usage
    return status


SCENARIOS = {
    "stopped": {
        "status": merged(),
        "level": None,
        "note": "nothing running, nothing left over",
    },
    "replay": {
        "status": merged(
            running=True, mode="replay", source="run1.jsonl", elapsed=68.4,
            audio_ms=68400, audio_total_s=300.0, responses=412, seq=398,
            viewers=3, live=True,
        ),
        "level": None,
        "note": "a saved recording playing to three phones",
    },
    "live-pending": {
        "status": merged(
            running=True, mode="mic", source="Headset Microphone @ 16000 Hz",
            elapsed=742.0, audio_ms=742000, audio_total_s=0, responses=2140,
            seq=2098, viewers=23, live=True, details_pending=True,
            engine_state="streaming", lag_seconds=0.42, reconnects=1,
            rollovers=0, silent_seconds=0, streamed_seconds=742.0,
            dropped_chunks=0, recording=None, capture="logs/live-demo.jsonl",
            usage={"billable_minutes": 12.4, "estimated_usd": 1.24,
                   "budget_remaining_usd": 8.76, "rate_per_minute": 0.1},
        ),
        "level": {"rms": 0.08, "peak": 0.42, "db": -22.0, "peak_db": -7.5,
                  "clipping": False},
        "note": "live, with talk details saved but not yet applied",
    },
    "live-confirm": {
        "status": merged(
            running=False, mode="replay", source=None,
            usage={"billable_minutes": 12.4, "estimated_usd": 1.24,
                   "budget_remaining_usd": 8.76, "rate_per_minute": 0.1},
        ),
        "level": {"rms": 0.02, "peak": 0.1, "db": -34.0, "peak_db": -20.0,
                  "clipping": False},
        "note": "the confirm step before a live run starts",
    },
    "error": {
        "status": merged(
            running=False, mode="mic",
            source="Headset Microphone @ 16000 Hz", elapsed=96.3,
            responses=184, seq=180, viewers=0,
            error="unauthenticated: invalid API key",
            engine_state=None, dropped_seconds=18.6,
            usage={"billable_minutes": 12.4, "estimated_usd": 1.24,
                   "budget_remaining_usd": 8.76, "rate_per_minute": 0.1},
        ),
        "level": None,
        "note": "a run that failed, with last-run values marked as past",
    },
}


def install_routes(page, scenario: dict) -> None:
    """Answer every request the page makes, dispatching on path.

    The page itself is served from `web/admin.html` rather than fetched from
    `/admin`, so **no admin token is involved anywhere in this script**. A
    token in a URL ends up in Playwright error messages, in a browser's own
    logs and in whatever terminal this is run from; the only way to be sure
    it does not is for it never to be here. The stylesheet and admin.js still
    come from the running server, so what is photographed is what is served.
    """
    import json
    from urllib.parse import urlparse

    page_html = (ROOT / "web" / "admin.html").read_text(encoding="utf-8")

    bodies = {
        "/admin/status": scenario["status"],
        "/admin/level": {"running": scenario["status"]["running"],
                         "level": scenario["level"]},
        "/admin/devices": {"devices": DEVICES},
        "/admin/session": {
            "details": DUMMY_DETAILS,
            "pending": scenario["status"]["details_pending"],
            "running": scenario["status"]["running"],
            "glossary_terms": ["RAG", "MCP", "LLM", "fine-tuning",
                               "embeddings", "transformers", "ChatGPT"],
        },
    }

    def handler(route, request):
        path = urlparse(request.url).path
        if path == "/admin":
            route.fulfill(status=200, content_type="text/html; charset=utf-8",
                          body=page_html)
        elif path in bodies:
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps(bodies[path]))
        else:
            route.continue_()

    page.route("**/*", handler)


def main() -> int:
    from playwright.sync_api import sync_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    url = BASE + "/admin"

    written: list[Path] = []
    with sync_playwright() as play:
        browser = play.chromium.launch()
        try:
            for name, scenario in SCENARIOS.items():
                for size_name, (width, height) in SIZES.items():
                    page = browser.new_page(
                        viewport={"width": width, "height": height},
                        device_scale_factor=1,
                    )
                    install_routes(page, scenario)
                    # Not "networkidle": the page polls once a second for ever,
                    # so the network is never idle and the wait would time out.
                    page.goto(url, wait_until="domcontentloaded")
                    if name.startswith("live"):
                        # The source dropdown drives the red Start button and
                        # the live-only rows; set it as an operator would.
                        page.select_option("#source", "live")
                        page.check("#arm")
                    if name == "live-confirm":
                        # ...and the confirm bar only appears after Start is
                        # pressed, which is the point of it.
                        page.click("#start")
                    page.wait_for_timeout(700)
                    target = OUT / f"admin-{name}-{size_name}.png"
                    page.screenshot(path=str(target), full_page=True)
                    written.append(target)
                    page.close()
        finally:
            browser.close()

    for path in written:
        print(f"  {path.relative_to(ROOT)}  {path.stat().st_size // 1024} kB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
