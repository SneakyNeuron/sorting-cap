"""Structural rules: one subprocess user, one prompts reader, schemas in one package."""

import ast
from pathlib import Path

from app.paths import REPO_ROOT

APP = REPO_ROOT / "app"


def _modules():
    return sorted(APP.rglob("*.py"))


def _rel(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT))


def _imports_subprocess(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(
            a.name.split(".")[0] == "subprocess" for a in node.names
        ):
            return True
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "subprocess":
            return True
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "__import__"
        ) and (
            node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "subprocess"
        ):
            return True
    return False


BANNED_CALLS = {
    ("os", "system"),
    ("os", "popen"),
    ("os", "execv"),
    ("os", "execvp"),
    ("os", "spawnv"),
    ("asyncio", "create_subprocess_exec"),
    ("asyncio", "create_subprocess_shell"),
}


def _banned_calls(tree: ast.AST) -> list[str]:
    found = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and (node.value.id, node.attr) in BANNED_CALLS
        ):
            found.append(f"{node.value.id}.{node.attr}")
    return found


def test_only_runner_uses_subprocess():
    users = [_rel(p) for p in _modules() if _imports_subprocess(ast.parse(p.read_text()))]
    assert users == ["app/setup/runner.py"]


def test_no_other_process_spawning():
    for path in _modules():
        assert _banned_calls(ast.parse(path.read_text())) == [], _rel(path)


def test_only_texts_and_paths_mention_prompts_dir():
    mentions = [
        _rel(p) for p in _modules() if "prompts/" in p.read_text() or "PROMPTS_DIR" in p.read_text()
    ]
    assert set(mentions) <= {"app/texts.py", "app/paths.py"}
    assert "app/texts.py" in mentions


def _prompt_path_suffixes() -> set[str]:
    """Every trailing part of every prompt file path: `system.md`, `chat/system.md`, …"""
    from app.texts import prompt_files

    suffixes = set()
    for rel in prompt_files():
        parts = ("prompts", *rel.parts)
        for i in range(len(parts)):
            suffixes.add("/".join(parts[i:]))
    return suffixes


def _string_constants(tree: ast.AST) -> list[str]:
    return [
        n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]


def test_no_prompt_files_read_outside_texts():
    """No module but `app/texts.py` names a file under `prompts/`.

    Workspace file names such as `profile.md` or `README.md` are not prompt reads.
    """
    suffixes = _prompt_path_suffixes()
    assert "system.md" in suffixes and "chat/system.md" in suffixes
    for path in _modules():
        if _rel(path) in ("app/texts.py", "app/paths.py"):
            continue
        for value in _string_constants(ast.parse(path.read_text())):
            normalized = value.replace("\\", "/").lstrip("./")
            hits = [s for s in suffixes if normalized == s or normalized.endswith("/" + s)]
            assert not hits, f"{_rel(path)} names prompt file {value!r}"


def test_prompt_path_check_allows_workspace_names():
    suffixes = _prompt_path_suffixes()
    for name in ("profile.md", "README.md", "AGENTS.md", "listing_filter_file.md"):
        assert name not in suffixes


def _imports_module(tree: ast.AST, module: str) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(a.name.split(".")[0] == module for a in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == module:
            return True
        if isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if (
                name in ("__import__", "import_module")
                and str(node.args[0].value).split(".")[0] == module
            ):
                return True
    return False


def test_no_module_imports_huggingface_hub():
    users = [
        _rel(p) for p in _modules() if _imports_module(ast.parse(p.read_text()), "huggingface_hub")
    ]
    assert users == []


def test_import_check_sees_every_import_form():
    for source in (
        "import huggingface_hub",
        "import huggingface_hub.utils as u",
        "from huggingface_hub import hf_hub_download",
        "__import__('huggingface_hub')",
        "importlib.import_module('huggingface_hub.file_download')",
    ):
        assert _imports_module(ast.parse(source), "huggingface_hub"), source
    assert not _imports_module(ast.parse("import tokenizers"), "huggingface_hub")


def test_tool_schemas_live_in_app_schemas():
    from app.tools import default_registry

    for tool in default_registry().tools():
        assert tool.input_model.__module__.startswith("app.schemas.")
        assert tool.output_model.__module__.startswith("app.schemas.")
