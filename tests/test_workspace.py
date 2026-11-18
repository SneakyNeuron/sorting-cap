import pytest

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


def test_uncreatable_path(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(WorkspaceError, match="cannot create"):
        ensure_workspace({"SORTINGCAP_WORKSPACE": str(blocker / "ws")})


def test_uncreatable_path_exits_one(tmp_path, monkeypatch, capsys):
    from app import cli

    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setenv("SORTINGCAP_WORKSPACE", str(blocker / "ws"))
    assert cli.main(["run", "help"]) == 1
    assert cli.main([]) == 1
