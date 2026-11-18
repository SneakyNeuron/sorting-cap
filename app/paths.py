"""Locations of the repository's data directories (`prompts/`, `defaults/`).

`uv sync` installs the project in editable mode, so both directories are found
next to the `app/` package at the repository root.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = REPO_ROOT / "prompts"
DEFAULTS_DIR = REPO_ROOT / "defaults"
TEXTS_DIR = DEFAULTS_DIR / "texts"
