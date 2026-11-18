"""The user's workspace directory: everything the user owns, as plain files."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

ENV_VAR = "SORTINGCAP_WORKSPACE"
DEFAULT_DIRNAME = "SortingCap"


class WorkspaceError(Exception):
    """The workspace cannot be resolved or created."""


def resolve_workspace(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    override = env.get(ENV_VAR, "").strip()
    if override:
        return Path(override).expanduser()
    home = env.get("HOME") or str(Path.home())
    return Path(home) / DEFAULT_DIRNAME


def ensure_workspace(env: Mapping[str, str] | None = None) -> Path:
    """Resolve the workspace and create it on first use."""
    path = resolve_workspace(env)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise WorkspaceError(f"cannot create workspace {path}: {exc.strerror or exc}") from exc
    if not path.is_dir():
        raise WorkspaceError(f"workspace {path} is not a directory")
    return path
