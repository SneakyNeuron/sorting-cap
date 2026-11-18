"""The user's workspace directory: everything the user owns, as plain files."""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from pathlib import Path

from app.errors import LocalizedError
from app.texts import workspace_note

ENV_VAR = "SORTINGCAP_WORKSPACE"
DEFAULT_DIRNAME = "SortingCap"
# Written into the workspace root when missing, never overwritten (R18).
NOTE_FILES = ("README.md", "AGENTS.md")


class WorkspaceError(LocalizedError):
    """The workspace cannot be resolved or created (text in the default language)."""


def resolve_workspace(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    override = env.get(ENV_VAR, "").strip()
    if override:
        return Path(override).expanduser()
    home = env.get("HOME") or str(Path.home())
    return Path(home) / DEFAULT_DIRNAME


def ensure_workspace(env: Mapping[str, str] | None = None) -> Path:
    """Resolve the workspace and create it on first use with mode 0700.

    The workspace notes (`README.md`, `AGENTS.md`) are written when missing.

    An existing directory keeps its mode (the user chose it); the REPL warns
    when it is open to other users (see `workspace_is_private`).
    """
    path = resolve_workspace(env)
    try:
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.mkdir(mode=0o700)
            path.chmod(0o700)
    except OSError as exc:
        raise WorkspaceError(
            "workspace.create_failed", path=str(path), detail=exc.strerror or str(exc)
        ) from exc
    if not path.is_dir():
        raise WorkspaceError("workspace.not_directory", path=str(path))
    write_missing_notes(path)
    return path


def write_missing_notes(workspace: Path) -> None:
    """Write each note that does not exist yet; an existing file (even edited) is kept.

    The file is created exclusively (`O_EXCL`), so a note the user writes at the
    same moment is never replaced.
    """
    for name in NOTE_FILES:
        target = workspace / name
        if target.exists() or target.is_symlink():
            continue
        text = workspace_note(name)
        try:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        except OSError as exc:
            raise WorkspaceError(
                "workspace.note_failed",
                name=name,
                path=str(workspace),
                detail=exc.strerror or str(exc),
            ) from exc
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
        except OSError as exc:
            target.unlink(missing_ok=True)
            raise WorkspaceError(
                "workspace.note_failed",
                name=name,
                path=str(workspace),
                detail=exc.strerror or str(exc),
            ) from exc


def workspace_is_private(path: Path) -> bool:
    """True when neither group nor others have any access to the workspace."""
    return not stat.S_IMODE(path.stat().st_mode) & 0o077
