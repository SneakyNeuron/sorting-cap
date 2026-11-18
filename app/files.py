"""Files only the user may read (mode 0600): atomic writes and created-if-missing files."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def write_private(path: Path, text: str | bytes) -> None:
    """Replace `path` with `text` (UTF-8, or bytes as they are) atomically; the content
    is never in a looser file.

    The text goes to a temporary file in the same directory, created with mode
    0600, which then replaces `path`. On any error the temporary file is removed
    and `path` is left as it was.
    """
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name.lstrip('.')}.", suffix=".tmp")
    try:
        if isinstance(text, bytes):
            with os.fdopen(fd, "wb") as fh:
                fh.write(text)
        else:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def private_file(path: Path) -> Path:
    """Create `path` if missing and make it readable only by the user (0600)."""
    os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600))
    path.chmod(0o600)
    return path
