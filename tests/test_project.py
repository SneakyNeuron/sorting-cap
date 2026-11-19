import subprocess
import sys
import tomllib

from app.paths import REPO_ROOT


def _lock_versions() -> dict[str, str]:
    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text())
    return {p["name"]: p["version"] for p in lock["package"] if "version" in p}


def _major(version: str) -> int:
    return int(version.split(".")[0])


def test_lock_within_t1_ranges():
    versions = _lock_versions()
    assert _major(versions["langchain"]) == 1
    assert _major(versions["pydantic"]) == 2
    assert _major(versions["prompt-toolkit"]) == 3
    for name in ("langchain-ollama", "langchain-deepseek", "rich", "httpx"):
        assert name in versions, name
    for name in ("pytest", "pytest-socket", "respx", "ruff"):
        assert name in versions, name


def test_no_python_dotenv():
    assert "python-dotenv" not in _lock_versions()


def test_build_backend_and_python():
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    assert project["build-system"]["build-backend"] == "uv_build"
    assert project["project"]["requires-python"] == ">=3.12,<3.13"
    assert project["project"]["scripts"]["sortingcap"] == "app.cli:main"


PHASE2_DEPENDENCIES = (
    "langgraph",
    "llama-index-core",
    "llama-index-readers-file",
    "beautifulsoup4",
    "onnxruntime",
    "tokenizers",
    "cryptography",
)


def _direct_dependencies() -> set[str]:
    import re

    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    return {re.split(r"[<>=!~\[ ;]", d, maxsplit=1)[0] for d in project["project"]["dependencies"]}


def test_phase2_dependencies_declared_and_locked():
    versions = _lock_versions()
    direct = _direct_dependencies()
    for name in PHASE2_DEPENDENCIES:
        assert name in direct, name
        assert name in versions, name
    assert _major(versions["langgraph"]) == 1


def test_no_torch_transformers_or_optimum():
    versions = _lock_versions()
    for name in ("torch", "transformers", "optimum"):
        assert name not in versions, name


def _collect(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_default_run_collects_no_live_test():
    """The live suite (T12) is opt-in: plain `pytest` deselects it via `addopts`."""
    default = _collect("tests/live")
    assert default.returncode == 5, default.stdout + default.stderr  # no tests collected
    assert "deselected" in default.stdout
    opted_in = _collect("-m", "live", "tests/live")
    assert opted_in.returncode == 0, opted_in.stdout + opted_in.stderr
    assert "tests/live/" in opted_in.stdout


def test_live_marker_registered():
    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())["tool"]["pytest"]
    assert any(m.startswith("live:") for m in config["ini_options"]["markers"])
