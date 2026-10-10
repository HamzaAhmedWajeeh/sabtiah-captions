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


settings = Settings()
