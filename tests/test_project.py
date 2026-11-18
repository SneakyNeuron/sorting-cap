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
