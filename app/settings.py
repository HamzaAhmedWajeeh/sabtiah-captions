"""Environment and YAML configuration.

Secrets come from the environment (.env on the venue laptop). Everything an
operator edits during a session lives in config/*.yaml so it survives restarts.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Written at the top of every saved session file, so someone who opens it
#: in an editor learns the two things that matter about it.
SESSION_FILE_HEADER = (
    "# This talk's details. Written by /admin - edit them there, not here.\n"
    "# LOCAL ONLY: gitignored, because speaker names and titles are not\n"
    "# public and this repository is. See config/session.example.yaml.\n"
)

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"
CONFIG_DIR = ROOT / "config"
LOG_DIR = ROOT / "logs"
RECORDING_DIR = ROOT / "recordings"


class Settings(BaseSettings):
    """Secrets and deploy-time values. Never serialized to a client."""

    model_config = SettingsConfigDict(
        env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    soniox_api_key: str = ""
    admin_token: str = ""
    public_url: str = "http://localhost:8000"

    #: Soniox credit, in dollars, that the whole project has to live inside
    #: (CLAUDE.md). Shown on `/admin` beside what has been spent.
    soniox_budget_usd: float = 10.0
    #: Dollars per streamed minute. Left at 0 because this repo has never
    #: recorded the real figure, and `app.usage` would rather show counted
    #: minutes than an invented dollar amount. Set it in `.env` to turn the
    #: dollar estimate on.
    soniox_rate_per_minute: float = 0.0


class Glossary(BaseModel):
    terms: list[str] = Field(default_factory=list)
    translation_terms: list[dict[str, str]] = Field(default_factory=list)


class SessionContext(BaseModel):
    """This talk's private details. **Never public.**

    Speaker names, the title and the operator's notes are not public, and the
    repository is. `config/session.yaml` is gitignored; the committed shape is
    `config/session.example.yaml`. These values reach exactly two places: the
    Soniox `context` object, and `/admin` behind its token. A test asserts
    they appear in neither the audience pages, the viewer socket, nor any log.

    An absent file is normal, not an error - a fresh clone has none, and the
    session simply starts with everything empty.
    """

    title: str = ""
    speakers: list[str] = Field(default_factory=list)
    companies: list[str] = Field(default_factory=list)
    #: Vocabulary for this talk only, merged with the public glossary.
    terms: list[str] = Field(default_factory=list)
    notes: str = ""

    def is_empty(self) -> bool:
        return not (self.title or self.speakers or self.companies
                    or self.terms or self.notes)

    def summary(self) -> dict[str, int | bool]:
        """Key names and counts, no values. Safe to log or to show anywhere.

        The point of this method is that there is an obvious thing to reach
        for when something needs to be recorded about the context, so nobody
        reaches for the context itself.
        """
        return {
            "title": bool(self.title),
            "speakers": len(self.speakers),
            "companies": len(self.companies),
            "terms": len(self.terms),
            "notes_chars": len(self.notes),
        }


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_glossary(path: Path | None = None) -> Glossary:
    return Glossary(**_load_yaml(path or CONFIG_DIR / "glossary.yaml"))


def load_session(path: Path | None = None) -> SessionContext:
    return SessionContext(**_load_yaml(path or CONFIG_DIR / "session.yaml"))


def save_session(session: SessionContext, path: Path | None = None) -> None:
    """Write the session file atomically.

    Temp file in the same directory, then `os.replace`, which is atomic on
    both Windows and POSIX. The operator may be saving this mid-talk while
    the session is reading it to build a context; a half-written YAML file
    would be a parse error at the worst possible moment.
    """
    target = Path(path or CONFIG_DIR / "session.yaml")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + f".{os.getpid()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            fh.write(SESSION_FILE_HEADER)
            yaml.safe_dump(
                session.model_dump(), fh, allow_unicode=True, sort_keys=False
            )
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def soniox_context(
    glossary_path: Path | None = None, session_path: Path | None = None
) -> dict:
    """The Soniox `context` object from the two YAML files.

    BUILD_SPEC section 6: `general` carries the title, speakers and companies
    as key-value items, `text` the operator's free notes, and `terms` /
    `translation_terms` come from the glossary. `speakers` is the cheapest
    quality win available and is usually the empty list - see
    `docs/soniox-notes.md`, "Proper nouns".

    `tools/probe_soniox.py` has its own copy of this from Phase 1. It is left
    alone deliberately: it is the tool that produced the five captures, and
    the value of being able to re-run it unchanged is worth more than removing
    a duplicated dict.
    """
    glossary = load_glossary(glossary_path)
    session = load_session(session_path)
    return build_context(glossary, session)


def build_context(glossary: Glossary, session: SessionContext) -> dict:
    """The same thing from values already in memory.

    Separate from `soniox_context` so the running session can build a context
    from the edits it is holding without a round trip through the file.
    """
    general: list[dict[str, str]] = []
    if session.title:
        general.append({"key": "title", "value": session.title})
    for name in session.speakers:
        general.append({"key": "speaker", "value": name})
    for company in session.companies:
        general.append({"key": "company", "value": company})
    # This talk's vocabulary on top of the public glossary, de-duplicated and
    # order-preserving so the file reads the way the operator wrote it.
    terms = list(glossary.terms)
    for term in session.terms:
        if term and term not in terms:
            terms.append(term)
    return {
        "general": general,
        "text": session.notes,
        "terms": terms,
        "translation_terms": glossary.translation_terms,
    }


settings = Settings()
