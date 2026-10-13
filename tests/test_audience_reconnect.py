"""The audience page must never sit frozen on a dead socket.

**The bug this file exists for.** On run9 (2026-10-13) the laptop's Wi-Fi was
switched off and back on. The laptop's own page recovered; the phone froze on
the last line before the disconnect and never came back, even once captions
were flowing again. Killing a radio drops the TCP connection without either
end sending a FIN, so the phone's WebSocket stayed in `OPEN`, `onclose` never
fired, and the reconnect path - which only runs from `onclose` - was never
reached.

A dead socket is indistinguishable from a quiet one, so silence is the only
signal available. The server heartbeats every 5 s and the page reconnects
after 12 s of hearing nothing.

The browser test needs Playwright, which is **not** a project dependency
(BUILD_SPEC section 2 lists what this project installs and a browser engine is
not on it). It is skipped unless Playwright is importable; run it with:

    uv run --with playwright pytest tests/test_audience_reconnect.py

Everything above that line is a plain offline test of the pieces.
"""

from __future__ import annotations

import contextlib
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.broadcast import PING_INTERVAL  # noqa: E402
from app.settings import WEB_DIR  # noqa: E402

APP_JS = (WEB_DIR / "app.js").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# The server half: a heartbeat fast enough for the watchdog to use
# ---------------------------------------------------------------------------


def test_the_heartbeat_is_fast_enough_for_the_client_watchdog():
    """The watchdog is only as good as the heartbeat feeding it.

    At the old 20 s a page would have to sit dead for most of a minute before
    noticing, which on a 40-minute talk is long enough to miss a whole topic.
    """
    silence = int(re.search(r"var SILENCE_LIMIT = (\d+)", APP_JS).group(1))
    assert PING_INTERVAL * 1000 * 2 <= silence, (
        f"the page waits {silence} ms for a heartbeat sent every "
        f"{PING_INTERVAL}s; it needs room for at least two to go missing"
    )
    assert silence <= 20000, "a frozen page should be noticed within seconds"


def test_the_heartbeat_itself_is_covered_where_it_lives():
    """`tests/test_broadcast.py` already proves a quiet session still gets a
    ping, and it runs against `PING_INTERVAL`, so it now exercises 5 s.

    Pointed at rather than copied: a second set of fake sockets and clocks
    here would be a worse copy of tests that already exist, and the one thing
    this file needs to add is the *relationship* between that interval and the
    page's watchdog, which is the test above.
    """
    broadcast_tests = (ROOT / "tests" / "test_broadcast.py").read_text(
        encoding="utf-8")
    assert "PING_INTERVAL" in broadcast_tests
    assert "ping" in broadcast_tests


# ---------------------------------------------------------------------------
# The client half, read from the shipped script
# ---------------------------------------------------------------------------


def test_the_page_treats_any_message_as_proof_of_life():
    """Including the heartbeat, which it otherwise ignores."""
    assert "heardAt = Date.now();" in APP_JS
    # ...and it is recorded *before* pings are discarded.
    heard = APP_JS.index("heardAt = Date.now();")
    ping = APP_JS.index("if (message.type === 'ping') { return; }")
    assert heard < ping, "the heartbeat is dropped before it counts as life"


def test_the_page_reconnects_after_silence_whatever_the_socket_claims():
    """`readyState === 1` is exactly what the frozen socket reported."""
    assert "function checkAlive()" in APP_JS
    assert "Date.now() - heardAt < SILENCE_LIMIT" in APP_JS
    assert "setInterval(checkAlive" in APP_JS


def test_the_page_reconnects_on_coming_back_online_and_on_waking():
    assert "addEventListener('online', wakeUp)" in APP_JS
    assert "visibilitychange" in APP_JS
    # The old visibility handler returned early on readyState <= 1, which is
    # the frozen case; it must not come back.
    assert "socket.readyState <= 1" not in APP_JS


def test_a_reconnect_shows_reconnecting_and_resyncs_from_a_snapshot():
    assert "setStatus('reconnecting')" in APP_JS
    # The server opens every connection with a full snapshot, so reconnecting
    # is the resync - which is what lets a frozen page catch up rather than
    # resume from a stale sequence number.
    assert "reconnecting is itself the resync" in APP_JS


def test_the_screen_page_shares_the_same_feed():
    """`/screen` must recover too, and it does by using one implementation."""
    screen = (WEB_DIR / "screen.js").read_text(encoding="utf-8")
    assert "SabtiahFeed" in screen
    assert "new WebSocket" not in screen, "the big screen has its own socket"


# ---------------------------------------------------------------------------
# The browser test: cut the network for real, restore it, check it catches up
# ---------------------------------------------------------------------------


try:
    import playwright.sync_api  # noqa: F401

    HAVE_PLAYWRIGHT = True
except ImportError:
    HAVE_PLAYWRIGHT = False

#: Only the browser test needs it. The checks above are plain offline tests
#: and must keep running in the ordinary suite.
needs_browser = pytest.mark.skipif(
    not HAVE_PLAYWRIGHT,
    reason="Playwright is not a project dependency; "
           "run with `uv run --with playwright`",
)

PORT = 8111
BASE = f"http://127.0.0.1:{PORT}"


@pytest.fixture(scope="module")
def live_server():
    """A real server on its own port, with a token only this test knows."""
    import os
    import urllib.request

    env = dict(os.environ, ADMIN_TOKEN="reconnect-test-token",
               PYTHONIOENCODING="utf-8")
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning"],
        cwd=ROOT, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(BASE + "/healthz", timeout=1).read()
                break
            except Exception:
                time.sleep(0.2)
        else:
            raise RuntimeError("the test server did not start")
        yield BASE
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


def paragraph_text(page) -> str:
    return page.evaluate(
        "() => document.getElementById('stream-en').innerText"
    )


@needs_browser
def test_a_page_whose_network_dies_reconnects_and_catches_up(live_server):
    """The run9 failure, reproduced and fixed, in a real browser.

    `context.set_offline(True)` is the closest thing to pulling the Wi-Fi: the
    socket stops delivering without a clean close, which is the condition the
    phone was stuck in.
    """
    import urllib.parse
    import urllib.request
    from playwright.sync_api import sync_playwright

    def admin(path: str, **params) -> None:
        params["token"] = "reconnect-test-token"
        url = f"{live_server}{path}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(url, method="POST")
        urllib.request.urlopen(request, timeout=10).read()

    with sync_playwright() as play:
        browser = play.chromium.launch()
        context = browser.new_context()
        page = context.new_page()
        try:
            page.goto(live_server + "/", wait_until="domcontentloaded")
            admin("/admin/start", capture="run5-english.jsonl", paced="false")
            page.wait_for_function(
                "() => document.getElementById('stream-en').innerText.length > 40",
                timeout=15000,
            )
            before = paragraph_text(page)
            assert before.strip(), "no captions before the outage"

            # --- pull the plug ---------------------------------------------
            context.set_offline(True)
            page.wait_for_function(
                "() => document.getElementById('state').textContent"
                "        .indexOf('reconnect') !== -1",
                timeout=25000,
            )

            # --- and put it back -------------------------------------------
            context.set_offline(False)
            page.wait_for_function(
                "() => document.getElementById('state').textContent"
                "        .indexOf('reconnect') === -1",
                timeout=30000,
            )

            # New captions must arrive, not just a reconnected socket.
            admin("/admin/stop")
            admin("/admin/start", capture="run1.jsonl", paced="false")
            page.wait_for_function(
                "(old) => document.getElementById('stream-en').innerText !== old",
                arg=before, timeout=20000,
            )
            after = paragraph_text(page)
            assert after != before, "the page never caught up after recovery"
            assert after.strip(), "the page came back empty"
        finally:
            with contextlib.suppress(Exception):
                admin("/admin/stop")
            browser.close()
