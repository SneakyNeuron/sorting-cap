"""Non-secret settings in `<workspace>/settings.json`, re-read on every command."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from app.providers.base import get_provider, provider_names

SETTINGS_FILE = "settings.json"


class SettingsError(Exception):
    """`settings.json` is unreadable or invalid. The file is left untouched."""


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    language: Literal["cs", "en"] = "cs"
    provider: str | None = None
    model: str | None = None
    ollama_base_url: str = "http://localhost:11434"
    openai_base_url: str | None = None
    setup_completed_at: datetime | None = None
    tour_shown: bool = False

    @field_validator("provider")
    @classmethod
    def _known_provider(cls, value: str | None) -> str | None:
        if value is not None and value not in provider_names():
            raise ValueError(f"unknown provider {value!r}; known: {', '.join(provider_names())}")
        return value

    @field_validator("ollama_base_url", "openai_base_url")
    @classmethod
    def _strip_slash(cls, value: str | None) -> str | None:
        return value.rstrip("/") if value else value

    def is_remote(self) -> bool:
        return self.provider is not None and get_provider(self.provider).is_remote(self)


def settings_path(workspace: Path) -> Path:
    return workspace / SETTINGS_FILE


def settings_exist(workspace: Path) -> bool:
    return settings_path(workspace).is_file()


def load_settings(workspace: Path) -> Settings:
    path = settings_path(workspace)
    if not path.exists():
        return Settings()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SettingsError(
            f"{path}: not valid JSON (line {exc.lineno}, column {exc.colno}): {exc.msg}"
        ) from exc
    except OSError as exc:
        raise SettingsError(f"{path}: cannot read: {exc.strerror or exc}") from exc
    if not isinstance(data, dict):
        raise SettingsError(f"{path}: expected a JSON object")
    try:
        return Settings.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(
            f"field '{'.'.join(str(p) for p in err['loc']) or '?'}': {err['msg']}"
            for err in exc.errors()
        )
        raise SettingsError(f"{path}: {problems}") from exc


def save_settings(workspace: Path, settings: Settings) -> None:
    path = settings_path(workspace)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(settings.model_dump_json(indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
