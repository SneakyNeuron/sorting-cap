"""Non-secret settings in `<workspace>/settings.json`, re-read on every command."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator
from pydantic_core import PydanticCustomError

from app.errors import LocalizedError
from app.providers.base import get_provider, provider_names
from app.providers.hosts import is_http_url

SETTINGS_FILE = "settings.json"


class SettingsError(LocalizedError):
    """`settings.json` is unreadable or invalid. The file is left untouched.

    Raised before the language is known: the caller resolves the text
    (`exc.text(texts)`), the default language when settings cannot be read.
    """


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
            raise PydanticCustomError(
                "unknown_provider",
                "unknown provider '{value}'; known: {known}",
                {"value": value, "known": ", ".join(provider_names())},
            )
        return value

    @field_validator("ollama_base_url", "openai_base_url")
    @classmethod
    def _http_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not is_http_url(value):
            raise PydanticCustomError(
                "http_url",
                "expected an http:// or https:// URL with a host, got '{value}'",
                {"value": value},
            )
        return value.strip().rstrip("/")

    def is_remote(self) -> bool:
        return self.provider is not None and get_provider(self.provider).is_remote(self)

    def endpoint_url(self) -> str | None:
        """The URL the configured provider's chat model talks to, if settings hold one."""
        return get_provider(self.provider).endpoint_url(self) if self.provider else None


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
            "settings.file_not_json", path=str(path), line=exc.lineno, column=exc.colno
        ) from exc
    except OSError as exc:
        raise SettingsError(
            "settings.file_unreadable", path=str(path), detail=exc.strerror or str(exc)
        ) from exc
    if not isinstance(data, dict):
        raise SettingsError("settings.file_not_object", path=str(path))
    try:
        return Settings.model_validate(data)
    except ValidationError as exc:
        raise SettingsError("settings.file_invalid", path=str(path), detail=exc) from exc


def save_settings(workspace: Path, settings: Settings) -> None:
    """Atomic write; the file is created with mode 0600 (only the user reads it)."""
    path = settings_path(workspace)
    fd, tmp = tempfile.mkstemp(dir=workspace, prefix=".settings.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(settings.model_dump_json(indent=2) + "\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
