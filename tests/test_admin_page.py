"""The operator page, checked without a JavaScript runtime.

**Why this file exists.** On 2026-10-13 a duplicate key in `admin.js`'s
element map - `capture:` listed twice, once for the `<select>` and once for a
`<td>` added later - silently rebound `els.capture` to the table cell. The
first line of `render()` then called `fillSelect` on a `<td>`, threw
"Array.prototype.map called on null or undefined", and every row on the
operator page showed an em dash while the engine's "Last error" row showed a
JavaScript message. It reached Hamza's screen during a live run.

Every Python test passed, because **nothing in the suite executes the page's
JavaScript**, and `uv` brings no JS runtime. These are the checks that can be
made statically, and they are the ones that would have caught that bug:

- no duplicate keys in the element map,
- every id the script looks up exists in the HTML, and vice versa,
- the server's status payload always carries the keys the page reads.

The last one is the real contract. The first two are cheap guards against the
specific class of mistake that got through.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.settings import WEB_DIR  # noqa: E402

ADMIN_JS = (WEB_DIR / "admin.js").read_text(encoding="utf-8")
ADMIN_HTML = (WEB_DIR / "admin.html").read_text(encoding="utf-8")


def element_map() -> list[tuple[str, str]]:
    """The `var els = {...}` block, as (key, element-id) pairs."""
    block = re.search(r"var els = \{(.*?)\n  \};", ADMIN_JS, re.S)
    assert block, "admin.js no longer has an `els` map; this test needs updating"
    return re.findall(
        r"(\w+):\s*document\.getElementById\('([^']+)'\)", block.group(1)
    )


# ---------------------------------------------------------------------------
# The bug that got through
# ---------------------------------------------------------------------------


def test_the_element_map_has_no_duplicate_keys():
    """A duplicate silently rebinds the earlier one, and JavaScript says
    nothing about it."""
    keys = [key for key, _ in element_map()]
    duplicates = sorted({k for k in keys if keys.count(k) > 1})
    assert not duplicates, (
        f"admin.js binds {duplicates} twice; the later one wins and the "
        f"earlier element becomes unreachable"
    )


def test_the_element_map_points_at_one_element_each():
    """Two names for one id is also a mistake, and the same kind."""
    ids = [element_id for _, element_id in element_map()]
    repeated = sorted({i for i in ids if ids.count(i) > 1})
    assert not repeated, f"two keys point at the same element: {repeated}"


@pytest.mark.parametrize("key,element_id", element_map())
def test_every_id_the_script_wants_exists_in_the_page(key, element_id):
    assert f'id="{element_id}"' in ADMIN_HTML, (
        f"admin.js reads els.{key} from #{element_id}, which the page does "
        f"not contain - it will be null at runtime"
    )


def test_every_id_in_the_page_is_used_by_the_script():
    """An id nobody reads is usually half of a rename that stopped early."""
    in_page = set(re.findall(r'id="([^"]+)"', ADMIN_HTML))
    used = {element_id for _, element_id in element_map()}
    # Anchors and containers the script never needs by id.
    allowed_unused: set[str] = {"meter"}
    orphans = in_page - used - allowed_unused
    assert not orphans, f"ids in admin.html that admin.js never reads: {orphans}"


def test_fillselect_refuses_a_non_select():
    """The guard that turns the duplicate-key bug into a no-op instead of a
    page-wide failure."""
    assert "if (!select || !select.options) { return; }" in ADMIN_JS


def test_render_is_sectioned_so_one_bad_field_cannot_blank_the_page():
    """Every row used to live or die together, because `render` was one
    straight line of assignments."""
    assert "function section(" in ADMIN_JS
    sections = re.findall(r"section\('(\w+)'", ADMIN_JS)
    assert len(sections) >= 5, f"render is barely sectioned: {sections}"


def test_a_page_error_does_not_land_in_the_engine_error_row():
    """A JavaScript fault reading "Last error" says the transcription failed,
    which is a lie about a different component."""
    assert "function pageProblem(" in ADMIN_JS
    assert 'id="page-problem"' in ADMIN_HTML
    # `fail` is what the fetch handlers call; it must not touch els.error.
    fail_body = re.search(r"function fail\(message\) \{(.*?)\n  \}",
                          ADMIN_JS, re.S)
    assert fail_body, "admin.js no longer has fail()"
    assert "els.error" not in fail_body.group(1)


# ---------------------------------------------------------------------------
# The contract: what the server promises the page
# ---------------------------------------------------------------------------


def status_keys_read_by_the_page() -> set[str]:
    """Every `status.X` the script reads."""
    return set(re.findall(r"\bstatus\.(\w+)", ADMIN_JS))


async def test_the_status_payload_carries_every_key_the_page_reads(tmp_path,
                                                                   monkeypatch):
    """The other half of the mismatch: a field the page reads and the server
    stopped sending.

    Checked in the state the page spends most of its life in - stopped, with
    no engine - because that is when the most fields are absent, and it is
    the state Hamza's page was in when it broke.
    """
    from app import main as server
    from app import usage
    from app.main import CaptionSession, admin_status
    from app.settings import settings

    monkeypatch.setattr(usage, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(settings, "admin_token", "t0ken")
    session = CaptionSession()
    monkeypatch.setattr(server, "session", session)

    body = json.loads((await admin_status(token="t0ken")).body)

    # Keys the page reads only when a live engine is attached are allowed to
    # be absent; the page renders an em dash for each.
    engine_only = {
        "engine_state", "lag_seconds", "reconnects", "rollovers",
        "silent_seconds", "streamed_seconds", "level", "dropped_chunks",
        "recording",
    }
    wanted = status_keys_read_by_the_page() - engine_only
    missing = sorted(wanted - set(body))
    assert not missing, (
        f"admin.js reads {missing} from /admin/status and the server does "
        f"not send them while stopped"
    )
    await session.shutdown()


def test_the_page_still_names_no_engine():
    """BUILD_SPEC section 8, re-checked here because this file is about the
    admin page specifically."""
    assert "SONIOX" not in ADMIN_JS.upper()
    assert "SONIOX" not in ADMIN_HTML.upper()


# ---------------------------------------------------------------------------
# The script has to be syntactically whole
# ---------------------------------------------------------------------------


def unterminated_string_lines(source: str) -> list[int]:
    """Line numbers where a quoted literal is still open at end of line.

    JavaScript has no multi-line single-quoted strings, so a quote left open
    at a line end is always a broken file. There is no JavaScript runtime in
    this project and `uv` brings none, so this is the cheapest check that
    catches the whole class.

    It exists because a literal newline once got written into a `.split()`
    call while editing admin.js, which made the **entire script** fail to
    parse. Every Python test still passed - they all read admin.js as text -
    and the operator page was simply dead in the browser.
    """
    bad: list[int] = []
    in_block_comment = False
    for number, line in enumerate(source.splitlines(), 1):
        stripped = line.strip()
        if in_block_comment:
            if "*/" in line:
                in_block_comment = False
            continue
        if stripped.startswith("/*"):
            if "*/" not in stripped:
                in_block_comment = True
            continue
        if stripped.startswith(("*", "//")):
            continue

        quote = None
        index = 0
        while index < len(line):
            char = line[index]
            if quote is not None:
                if char == "\\":
                    index += 2
                    continue
                if char == quote:
                    quote = None
            elif char in "'\"":
                quote = char
            elif char == "/" and line[index + 1:index + 2] == "/":
                break  # the rest of the line is a comment
            index += 1
        if quote is not None:
            bad.append(number)
    return bad


@pytest.mark.parametrize("name", ["admin.js", "app.js", "screen.js"])
def test_served_scripts_have_no_unterminated_string(name):
    source = (WEB_DIR / name).read_text(encoding="utf-8")
    bad = unterminated_string_lines(source)
    assert not bad, (
        f"{name} leaves a string open at line(s) {bad}; the whole file will "
        f"fail to parse and the page will be dead in the browser"
    )


def test_the_unterminated_string_check_catches_what_it_claims_to():
    """A guard is worth nothing until it fails on the thing it guards."""
    broken = "var a = 'one';\nvar b = 'two\n';\n"
    # Both lines: the literal opens on 2 and the orphaned closing quote on 3
    # opens one of its own. That is what a newline pasted into a string looks
    # like, and flagging both ends of it is the useful answer.
    assert unterminated_string_lines(broken) == [2, 3]

    assert unterminated_string_lines("var a = 'fine';\n") == []
    assert unterminated_string_lines("var a = 'a\\'b';\n") == []
    # An apostrophe inside double quotes, or in a comment, is not a bug.
    assert unterminated_string_lines('var a = "it\'s fine";\n') == []
    assert unterminated_string_lines("// don't trip on this\n") == []
    assert unterminated_string_lines(" * don't trip on this either\n") == []
