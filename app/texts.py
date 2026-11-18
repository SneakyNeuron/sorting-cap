"""Per-language texts: prompts, prewritten texts and the UI message catalog.

This is the only module that reads `prompts/`. Missing keys or files fail loudly.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

from app.paths import PROMPTS_DIR, TEXTS_DIR

LANGUAGES = ("cs", "en")
DEFAULT_LANGUAGE = "cs"


class TextsError(Exception):
    """A text, prompt or message key is missing or unreadable."""


def _check_language(language: str) -> str:
    if language not in LANGUAGES:
        raise TextsError(f"unsupported language: {language!r}")
    return language


@cache
def _catalog(language: str, texts_dir: Path) -> dict[str, str]:
    path = texts_dir / language / "messages.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TextsError(f"message catalog not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise TextsError(f"message catalog is not valid JSON: {path}: {exc}") from exc
    if not isinstance(data, dict) or not all(isinstance(v, str) for v in data.values()):
        raise TextsError(f"message catalog must map keys to strings: {path}")
    return data


def message_keys(language: str, texts_dir: Path = TEXTS_DIR) -> set[str]:
    return set(_catalog(_check_language(language), texts_dir))


def has_message(language: str, key: str, texts_dir: Path = TEXTS_DIR) -> bool:
    return key in _catalog(_check_language(language), texts_dir)


def prompt_files(prompts_dir: Path = PROMPTS_DIR) -> list[Path]:
    """Every prompt file, as paths relative to the prompts directory."""
    return sorted(p.relative_to(prompts_dir) for p in prompts_dir.rglob("*.md"))


class Texts:
    """All texts of one language."""

    def __init__(
        self,
        language: str = DEFAULT_LANGUAGE,
        *,
        prompts_dir: Path = PROMPTS_DIR,
        texts_dir: Path = TEXTS_DIR,
    ) -> None:
        self.language = _check_language(language)
        self._prompts_dir = prompts_dir
        self._texts_dir = texts_dir

    def message(self, key: str, **values: object) -> str:
        catalog = _catalog(self.language, self._texts_dir)
        try:
            template = catalog[key]
        except KeyError as exc:
            raise TextsError(f"message key {key!r} missing for language {self.language!r}") from exc
        return template.format(**values) if values else template

    def has(self, key: str) -> bool:
        return key in _catalog(self.language, self._texts_dir)

    def prompt(self, agent: str, name: str) -> str:
        return self._read(self._prompts_dir / self.language / agent / f"{name}.md")

    def prewritten(self, name: str) -> str:
        return self._read(self._texts_dir / self.language / f"{name}.md")

    @staticmethod
    def _read(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise TextsError(f"text file not found: {path}") from exc
