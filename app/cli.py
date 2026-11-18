"""Entry point: `sortingcap` (chat), `sortingcap run <command>` (headless), `--version`."""

import os

# T15: LangSmith tracing is always off. langsmith resolves
# `get_env_var("TRACING_V2", default=get_env_var("TRACING"))` over the
# `LANGSMITH_` then `LANGCHAIN_` namespaces, so all four names are forced.
TRACING_VARS = (
    "LANGSMITH_TRACING_V2",
    "LANGSMITH_TRACING",
    "LANGCHAIN_TRACING_V2",
    "LANGCHAIN_TRACING",
)


def disable_tracing() -> None:
    """Force tracing off in this process; must run before langchain/langsmith import.

    langsmith caches its environment lookups, so this runs at module import.
    """
    for name in TRACING_VARS:
        os.environ[name] = "false"
    os.environ.pop("LANGSMITH_GATEWAY", None)


disable_tracing()

import sys  # noqa: E402
from collections.abc import Sequence  # noqa: E402
from importlib.metadata import version  # noqa: E402


def package_version() -> str:
    return version("sortingcap")


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] in (["--version"], ["-V"]):
        print(f"sortingcap {package_version()}")
        return 0
    if args[:1] == ["run"]:
        from app.tools.headless import run_headless

        return run_headless(args[1:])
    if args:
        print("usage: sortingcap [--version] | sortingcap run <command> [args] [--json] [--yes]")
        return 2
    from app.repl import run_repl

    return run_repl()


if __name__ == "__main__":
    raise SystemExit(main())
