"""Environment and YAML configuration.

Secrets come from the environment (.env on the venue laptop). Everything an
operator edits during a session lives in config/*.yaml so it survives restarts.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

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
    title: str = ""
    speakers: list[str] = Field(default_factory=list)
    companies: list[str] = Field(default_factory=list)
    notes: str = ""


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
    target = path or CONFIG_DIR / "session.yaml"
    with target.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(
            session.model_dump(), fh, allow_unicode=True, sort_keys=False
        )


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
    general: list[dict[str, str]] = []
    if session.title:
        general.append({"key": "title", "value": session.title})
    for name in session.speakers:
        general.append({"key": "speaker", "value": name})
    for company in session.companies:
        general.append({"key": "company", "value": company})
    return {
        "general": general,
        "text": session.notes,
        "terms": glossary.terms,
        "translation_terms": glossary.translation_terms,
    }


settings = Settings()
