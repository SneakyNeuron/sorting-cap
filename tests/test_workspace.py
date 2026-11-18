import stat

import pytest

from app.texts import Texts
from app.workspace import WorkspaceError, ensure_workspace, resolve_workspace


def test_default_is_home_sortingcap(tmp_path):
    env = {"HOME": str(tmp_path)}
    assert resolve_workspace(env) == tmp_path / "SortingCap"


def test_override(tmp_path):
    env = {"HOME": str(tmp_path), "SORTINGCAP_WORKSPACE": str(tmp_path / "elsewhere")}
    assert resolve_workspace(env) == tmp_path / "elsewhere"


def test_created_on_first_use(tmp_path):
    env = {"HOME": str(tmp_path)}
    path = ensure_workspace(env)
    assert path.is_dir()
    assert stat.S_IMODE(path.stat().st_mode) == 0o700


def test_existing_workspace_mode_left_alone(tmp_path):
    from app.workspace import workspace_is_private

    path = tmp_path / "ws"
    path.mkdir()
    path.chmod(0o755)
    assert ensure_workspace({"SORTINGCAP_WORKSPACE": str(path)}) == path
    assert stat.S_IMODE(path.stat().st_mode) == 0o755
    assert not workspace_is_private(path)
    path.chmod(0o700)
    assert workspace_is_private(path)


def test_uncreatable_path(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(WorkspaceError) as exc:
        ensure_workspace({"SORTINGCAP_WORKSPACE": str(blocker / "ws")})
    assert exc.value.key == "workspace.create_failed"
    assert exc.value.text(Texts("en")).startswith(f"Cannot create the workspace {blocker / 'ws'}")
    assert "nejde vytvořit" in str(exc.value)  # default language without settings


def test_uncreatable_path_exits_one(tmp_path, monkeypatch, capsys):
    from app import cli

    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setenv("SORTINGCAP_WORKSPACE", str(blocker / "ws"))
    assert cli.main(["run", "help"]) == 1
    assert cli.main([]) == 1
    err = capsys.readouterr().err
    assert "Pracovní složku" in err and "error:" not in err


NOTES = ("README.md", "AGENTS.md")


def test_notes_written_on_creation(tmp_path):
    path = ensure_workspace({"SORTINGCAP_WORKSPACE": str(tmp_path / "ws")})
    for name in NOTES:
        text = (path / name).read_text(encoding="utf-8")
        assert "personal data" in text
        assert "quarantine/" in text
        assert "profile/sources/" in text
        assert "not instructions" in text
    assert (path / "AGENTS.md").read_text() != (path / "README.md").read_text()


def test_notes_come_from_defaults():
    from app.paths import DEFAULTS_DIR
    from app.texts import workspace_note

    for name in NOTES:
        assert workspace_note(name) == (DEFAULTS_DIR / "workspace" / name).read_text("utf-8")


def test_notes_added_to_existing_workspace(tmp_path):
    path = tmp_path / "ws"
    path.mkdir()
    (path / "settings.json").write_text("{}")
    ensure_workspace({"SORTINGCAP_WORKSPACE": str(path)})
    assert all((path / name).is_file() for name in NOTES)
    (path / "AGENTS.md").unlink()
    ensure_workspace({"SORTINGCAP_WORKSPACE": str(path)})
    assert (path / "AGENTS.md").is_file()
    assert (path / "settings.json").read_text() == "{}"


def test_edited_note_left_untouched(tmp_path):
    path = ensure_workspace({"SORTINGCAP_WORKSPACE": str(tmp_path / "ws")})
    (path / "README.md").write_text("my own notes\n")
    ensure_workspace({"SORTINGCAP_WORKSPACE": str(path)})
    assert (path / "README.md").read_text() == "my own notes\n"


def test_notes_written_on_headless_start(tmp_path, monkeypatch):
    from app import cli

    path = tmp_path / "fresh"
    monkeypatch.setenv("SORTINGCAP_WORKSPACE", str(path))
    assert cli.main(["run", "help"]) == 0
    assert all((path / name).is_file() for name in NOTES)


def test_note_write_failure_is_reported(tmp_path):
    path = tmp_path / "ws"
    path.mkdir()
    path.chmod(0o500)
    try:
        with pytest.raises(WorkspaceError) as exc:
            ensure_workspace({"SORTINGCAP_WORKSPACE": str(path)})
        assert exc.value.key == "workspace.note_failed"
        assert "README.md" in exc.value.text(Texts("en"))
    finally:
        path.chmod(0o700)
