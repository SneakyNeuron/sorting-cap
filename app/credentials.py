"""Remote API keys: environment first, then `<workspace>/.env` (mode 0600).

During `-setup` a newly typed key is held in memory (`overrides`, ahead of the
file) until verification succeeds; only then is it written to `.env`.

Keys are never written to `settings.json`, logs, output or the REPL history.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from app.files import write_private

NOVITA_KEY = "NOVITA_API_KEY"
OPENAI_KEY = "SORTINGCAP_OPENAI_API_KEY"
KNOWN_KEYS = (NOVITA_KEY, OPENAI_KEY)
ENV_FILE = ".env"


def _parse_env(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def check_value(name: str, value: str) -> None:
    """Refuse an empty value or one with control characters (newlines included)."""
    if not value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError(f"{name}: empty value or control characters")


def mask(value: str | None) -> str:
    if not value:
        return "—"
    if len(value) <= 8:
        return "****"
    return f"{value[:3]}…{value[-2:]}"


@dataclass
class Credentials:
    workspace: Path
    env: Mapping[str, str] | None = field(default=None, repr=False)
    overrides: Mapping[str, str] | None = field(default=None, repr=False)

    @property
    def env_file(self) -> Path:
        return self.workspace / ENV_FILE

    def _file_values(self) -> dict[str, str]:
        try:
            return _parse_env(self.env_file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}

    def from_env(self, name: str) -> str | None:
        env = os.environ if self.env is None else self.env
        return env.get(name) or None

    def get(self, name: str) -> str | None:
        """Environment, then an unsaved setup override, then `.env`."""
        value = self.from_env(name) or (self.overrides or {}).get(name)
        if value:
            return value
        return self._file_values().get(name) or None

    def set(self, name: str, value: str) -> None:
        """Create or update `.env` with mode 0600, keeping other lines.

        Values with control characters (newlines included) are refused, so a
        pasted value cannot add or override other lines. The new content goes to
        a temporary file created with mode 0600 and replaces `.env` atomically,
        so the key is never written into a file with looser permissions.
        """
        check_value(name, value)
        path = self.env_file
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        out, replaced = [], False
        for line in lines:
            stripped = line.strip().removeprefix("export ").lstrip()
            if stripped.split("=", 1)[0].strip() == name and "=" in stripped:
                if not replaced:
                    out.append(f"{name}={value}")
                    replaced = True
                continue
            out.append(line)
        if not replaced:
            out.append(f"{name}={value}")
        write_private(path, "\n".join(out) + "\n")
