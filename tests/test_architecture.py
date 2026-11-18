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


def test_no_prompt_files_read_outside_texts():
    for path in _modules():
        if _rel(path) in ("app/texts.py", "app/paths.py"):
            continue
        text = path.read_text()
        assert '.md"' not in text and ".md'" not in text, _rel(path)


def test_tool_schemas_live_in_app_schemas():
    from app.tools import default_registry

    for tool in default_registry().tools():
        assert tool.input_model.__module__.startswith("app.schemas.")
        assert tool.output_model.__module__.startswith("app.schemas.")
