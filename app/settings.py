"""Non-secret settings in `<workspace>/settings.json`, re-read on every command."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator
from pydantic_core import PydanticCustomError

from app.errors import LocalizedError
from app.files import write_private
from app.providers.base import get_provider, provider_names
from app.providers.hosts import is_http_url
from app.texts import DEFAULT_LANGUAGE, Language

SETTINGS_FILE = "settings.json"


class SettingsError(LocalizedError):
    """`settings.json` is unreadable or invalid. The file is left untouched.

    Raised before the language is known: the caller resolves the text
    (`exc.text(texts)`), the default language when settings cannot be read.
    """


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    language: Language = DEFAULT_LANGUAGE
    provider: str | None = None
    model: str | None = None
    ollama_base_url: str = "http://localhost:11434"
    openai_base_url: str | None = None
    setup_completed_at: datetime | None = None
    tour_shown: bool = False
    # Injection-classifier score at or above which guarded input is flagged (0 < t < 1).
    guard_threshold: float = 0.5

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

    @field_validator("guard_threshold")
    @classmethod
    def _open_unit_interval(cls, value: float) -> float:
        if not 0 < value < 1:  # also rejects NaN
            raise PydanticCustomError(
                "guard_threshold",
                "expected a number greater than 0 and less than 1, got {value}",
                {"value": f"{value:g}"},
            )
        return value

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
    write_private(settings_path(workspace), settings.model_dump_json(indent=2) + "\n")
