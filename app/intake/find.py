"""`find_files`: locate a file the user describes vaguely, by name (Phase 3 R18, T11).

Reads directory listings only, never file contents. Walks at most `MAX_DEPTH` levels
below each root, breadth first; skips dot directories, symlinks and the workspace's
`quarantine/`; stops after `MAX_ENTRIES` directory entries per call. Lists only the
supported source types whose name contains the query, ignoring case and diacritics,
and returns at most `MAX_RESULTS` absolute paths, the most recently modified first.

Without a folder the roots are the current directory, `~/Downloads`, `~/Documents`,
`~/Desktop` and then `~`; a directory already walked from an earlier root is not
walked again, so the named folders are searched before the home folder can use up
the entry cap.

The paths are returned as found; callers escape controls when they show them.
"""

from __future__ import annotations

import os
import unicodedata
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from app.guard.quarantine import QUARANTINE_DIR
from app.intake.detect import expand_home, unquote
from app.intake.sources import EXTENSIONS, resolve_path

MAX_DEPTH = 3
MAX_ENTRIES = 5000
MAX_RESULTS = 20
HOME_FOLDERS = ("Downloads", "Documents", "Desktop")

FindError = Literal["empty_name", "not_found", "not_a_directory", "quarantine"]


@dataclass(frozen=True)
class FindResult:
    """The found paths, newest first, or an error the agent sees. `truncated` is set
    when the entry cap stopped the walk or more than `MAX_RESULTS` files matched."""

    paths: list[str] = field(default_factory=list)
    error: FindError | None = None
    truncated: bool = False
    scanned: int = 0


def fold(text: str) -> str:
    """`text` without diacritics, case-folded."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


def _real(path: Path) -> str:
    return os.path.realpath(path)


def _inside(path: str, folder: str) -> bool:
    return path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)


def _roots(folder: str | None, cwd: Path, home: Path) -> list[Path]:
    if folder is not None:
        # unquoted and `~` expanded as the allow rule does: `~` means `home`, as in
        # the default roots, not the HOME variable
        return [resolve_path(expand_home(unquote(folder), home), cwd)]
    return [cwd, *(home / name for name in HOME_FOLDERS), home]


def find_files(
    name: str,
    folder: str | None = None,
    *,
    cwd: Path,
    workspace: Path,
    home: Path | None = None,
) -> FindResult:
    """Supported-type files whose name contains `name`, under `folder` (resolved like a
    source path; it must be an existing directory) or the default roots (R18, T11)."""
    query = fold(name.strip())
    if not query:
        return FindResult(error="empty_name")
    home = home if home is not None else Path.home()
    quarantine = _real(workspace / QUARANTINE_DIR)
    roots = _roots(folder, cwd, home)
    if folder is not None:
        root = roots[0]
        if not root.exists():
            return FindResult(error="not_found")
        if not root.is_dir():
            return FindResult(error="not_a_directory")
        if _inside(_real(root), quarantine):
            return FindResult(error="quarantine")

    visited: set[str] = set()
    matches: list[tuple[float, str]] = []
    scanned = 0
    capped = False
    for root in roots:
        if capped:
            break
        if not root.is_dir():
            continue  # a default root that does not exist
        queue: deque[tuple[str, int]] = deque([(str(root.absolute()), 0)])
        while queue and not capped:
            directory, depth = queue.popleft()
            real = _real(Path(directory))
            if real in visited or _inside(real, quarantine):
                continue
            visited.add(real)
            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        if scanned >= MAX_ENTRIES:
                            capped = True
                            break
                        scanned += 1
                        try:
                            if entry.is_symlink():
                                continue
                            if entry.is_dir(follow_symlinks=False):
                                if depth < MAX_DEPTH and not entry.name.startswith("."):
                                    queue.append((entry.path, depth + 1))
                                continue
                            if not entry.is_file(follow_symlinks=False):
                                continue
                            if os.path.splitext(entry.name)[1].lower() not in EXTENSIONS:
                                continue
                            if query not in fold(entry.name):
                                continue
                            mtime = entry.stat(follow_symlinks=False).st_mtime
                        except OSError:
                            continue
                        matches.append((mtime, entry.path))
            except OSError:
                continue  # unreadable directory

    matches.sort(key=lambda m: (-m[0], m[1]))
    return FindResult(
        paths=[path for _mtime, path in matches[:MAX_RESULTS]],
        truncated=capped or len(matches) > MAX_RESULTS,
        scanned=scanned,
    )
