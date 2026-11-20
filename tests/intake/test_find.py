"""`find_files`: a name search over directory listings (Phase 3 R18, T11)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.intake import find as find_module
from app.intake.find import MAX_ENTRIES, MAX_RESULTS, find_files


def touch(path: Path, mtime: float | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "tree"
    root.mkdir()
    return root


def names(result) -> list[str]:
    return sorted(Path(p).name for p in result.paths)


def test_name_match_ignores_case_and_diacritics(tree, workspace):
    touch(tree / "Životopis 2026.PDF")
    touch(tree / "zivotopis-old.md")
    touch(tree / "ZIVOTOPIS.txt")
    touch(tree / "other.pdf")
    result = find_files("životopis", str(tree), cwd=tree, workspace=workspace)
    assert result.error is None
    assert names(result) == sorted(["Životopis 2026.PDF", "ZIVOTOPIS.txt", "zivotopis-old.md"])
    assert names(find_files("ZIVOTOPIS", str(tree), cwd=tree, workspace=workspace)) == names(result)


def test_paths_are_absolute(tree, workspace):
    touch(tree / "a" / "cv.pdf")
    result = find_files("cv", "a", cwd=tree, workspace=workspace)
    assert result.paths == [str(tree / "a" / "cv.pdf")]
    assert all(Path(p).is_absolute() for p in result.paths)


def test_folder_with_tilde(isolated_env, workspace, tmp_path):
    touch(isolated_env / "Downloads" / "cv.pdf")
    result = find_files("cv", "~/Downloads", cwd=tmp_path, workspace=workspace)
    assert result.paths == [str(isolated_env / "Downloads" / "cv.pdf")]


def test_folder_tilde_uses_the_home_parameter(isolated_env, workspace, tmp_path):
    other = tmp_path / "other-home"
    touch(other / "Downloads" / "cv-other.pdf")
    touch(isolated_env / "Downloads" / "cv.pdf")
    result = find_files("cv", "~/Downloads", cwd=tmp_path, workspace=workspace, home=other)
    assert result.paths == [str(other / "Downloads" / "cv-other.pdf")]


@pytest.mark.parametrize("folder", ["'~/Downloads'", '" ~/Downloads "', "  ~/Downloads  "])
def test_folder_unquoted_and_expanded_like_the_allow_rule(
    isolated_env, workspace, tmp_path, folder
):
    touch(isolated_env / "Downloads" / "cv.pdf")
    result = find_files("cv", folder, cwd=tmp_path, workspace=workspace, home=isolated_env)
    assert result.paths == [str(isolated_env / "Downloads" / "cv.pdf")]


def test_depth_three(tree, workspace):
    touch(tree / "cv0.md")
    touch(tree / "a" / "cv1.md")
    touch(tree / "a" / "b" / "c" / "cv3.md")
    touch(tree / "a" / "b" / "c" / "d" / "cv4.md")
    result = find_files("cv", str(tree), cwd=tree, workspace=workspace)
    assert names(result) == ["cv0.md", "cv1.md", "cv3.md"]


def test_dot_and_symlinked_directories_skipped(tree, workspace, tmp_path):
    touch(tree / ".hidden" / "cv.md")
    outside = tmp_path / "outside"
    touch(outside / "cv-outside.md")
    (tree / "link").symlink_to(outside, target_is_directory=True)
    (tree / "cv-link.md").symlink_to(outside / "cv-outside.md")
    touch(tree / ".cv-dotfile.md")
    result = find_files("cv", str(tree), cwd=tree, workspace=workspace)
    # dot files are listed; dot directories, symlinked directories and symlinks are not
    assert names(result) == [".cv-dotfile.md"]


def test_only_supported_types(tree, workspace):
    for name in ("cv.txt", "cv.md", "cv.pdf", "cv.html", "cv.HTM", "cv.docx", "cv.md5", "cv"):
        touch(tree / name)
    (tree / "cv-dir.md").mkdir()
    result = find_files("cv", str(tree), cwd=tree, workspace=workspace)
    assert names(result) == ["cv.HTM", "cv.html", "cv.md", "cv.pdf", "cv.txt"]


def test_at_most_twenty_newest_first(tree, workspace):
    for i in range(MAX_RESULTS + 5):
        touch(tree / f"cv-{i:02}.md", mtime=1_700_000_000 + i)
    result = find_files("cv", str(tree), cwd=tree, workspace=workspace)
    assert MAX_RESULTS == 20
    assert [Path(p).name for p in result.paths] == [
        f"cv-{i:02}.md" for i in range(MAX_RESULTS + 4, 4, -1)
    ]
    assert result.truncated


def test_entry_cap(tree, workspace):
    assert MAX_ENTRIES == 5000
    for i in range(MAX_ENTRIES + 200):
        (tree / f"f{i}.bin").touch()
    result = find_files("cv", str(tree), cwd=tree, workspace=workspace)
    assert result.scanned == MAX_ENTRIES
    assert result.truncated
    assert result.paths == []


def test_entry_cap_is_per_call_across_roots(tmp_path, isolated_env, workspace, monkeypatch):
    monkeypatch.setattr(find_module, "MAX_ENTRIES", 3)
    cwd = tmp_path / "cwd"
    for i in range(5):
        touch(cwd / f"x{i}.bin")
    touch(isolated_env / "Downloads" / "cv.pdf")
    result = find_files("cv", None, cwd=cwd, workspace=workspace)
    assert result.scanned == 3
    assert result.paths == []


def test_missing_folder_is_an_error(tree, workspace):
    result = find_files("cv", str(tree / "nope"), cwd=tree, workspace=workspace)
    assert result.error == "not_found"
    assert result.paths == []
    touch(tree / "file.md")
    assert find_files("cv", str(tree / "file.md"), cwd=tree, workspace=workspace).error == (
        "not_a_directory"
    )


def test_empty_name_is_an_error(tree, workspace):
    assert find_files("  ", str(tree), cwd=tree, workspace=workspace).error == "empty_name"


def test_default_roots(tmp_path, isolated_env, workspace):
    cwd = tmp_path / "cwd"
    touch(cwd / "cv-here.md")
    touch(isolated_env / "cv-home.md")
    touch(isolated_env / "Downloads" / "cv-dl.pdf")
    touch(isolated_env / "Documents" / "x" / "y" / "z" / "cv-doc.txt")  # depth 3 below Documents
    touch(isolated_env / "Desktop" / "cv-desk.html")
    result = find_files("cv", None, cwd=cwd, workspace=workspace)
    assert result.error is None
    assert names(result) == ["cv-desk.html", "cv-dl.pdf", "cv-doc.txt", "cv-here.md", "cv-home.md"]
    # every file once, though `~` contains the other roots
    assert len(result.paths) == len(set(result.paths))


def test_quarantine_never_listed(tmp_path, isolated_env):
    workspace = isolated_env / "SortingCap"
    touch(workspace / "quarantine" / "cv-flagged.md")
    touch(workspace / "profile" / "cv-kept.md")
    result = find_files("cv", None, cwd=workspace, workspace=workspace)
    assert names(result) == ["cv-kept.md"]
    inside = find_files("cv", str(workspace / "quarantine"), cwd=tmp_path, workspace=workspace)
    assert inside.error == "quarantine"
    assert inside.paths == []


def test_no_file_content_read(tree, workspace, monkeypatch):
    touch(tree / "cv.md")

    def boom(*_args, **_kwargs):
        raise AssertionError("file content read")

    monkeypatch.setattr(Path, "read_bytes", boom)
    monkeypatch.setattr(Path, "read_text", boom)
    monkeypatch.setattr(Path, "open", boom)
    monkeypatch.setattr("builtins.open", boom)
    assert names(find_files("cv", str(tree), cwd=tree, workspace=workspace)) == ["cv.md"]


@pytest.mark.parametrize("folder", [".ssh", ".config/app", "a/../.ssh"])
def test_hidden_folder_is_refused(tree, workspace, folder):
    # validation fix D7: no dot directory as a root, also below or through `..`
    touch(tree / ".ssh" / "cv.md")
    touch(tree / ".config" / "app" / "cv.md")
    (tree / "a").mkdir()
    result = find_files("cv", folder, cwd=tree, workspace=workspace)
    assert (result.paths, result.error) == ([], "hidden_folder")


def test_symlink_into_a_hidden_folder_is_refused(tree, workspace):
    touch(tree / ".ssh" / "cv.md")
    (tree / "link").symlink_to(tree / ".ssh")
    result = find_files("cv", "link", cwd=tree, workspace=workspace)
    assert result.error == "hidden_folder"
