import stat

import pytest

from app import files
from app.files import private_file, write_private


def test_write_private_creates_0600_and_replaces(workspace):
    path = workspace / "settings.json"
    path.write_text("old")
    path.chmod(0o644)
    write_private(path, "new\n")
    assert path.read_text() == "new\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert [p.name for p in workspace.iterdir()] == ["settings.json"]


def test_write_private_failure_keeps_file_and_leaves_no_temp(workspace, monkeypatch):
    path = workspace / ".env"
    path.write_text("keep\n")

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(files.os, "replace", boom)
    with pytest.raises(OSError):
        write_private(path, "lost\n")
    assert path.read_text() == "keep\n"
    assert [p.name for p in workspace.iterdir()] == [".env"]


def test_private_file_creates_or_tightens_and_keeps_content(workspace):
    path = workspace / ".history"
    assert private_file(path) == path
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    path.write_text("line\n")
    path.chmod(0o644)
    private_file(path)
    assert path.read_text() == "line\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_write_private_bytes(workspace):
    path = workspace / "blob.bin"
    write_private(path, b"\x00\xffraw")
    assert path.read_bytes() == b"\x00\xffraw"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
