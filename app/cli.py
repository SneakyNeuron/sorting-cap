"""Entry point: `sortingcap` (chat), `sortingcap run <command>` (headless), `--version`."""

import os

# T15: tracing is always off. Set before anything imports langchain or langsmith,
# because langsmith caches its environment lookups.
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ.pop("LANGSMITH_GATEWAY", None)

import sys  # noqa: E402
from collections.abc import Sequence  # noqa: E402
from importlib.metadata import version  # noqa: E402


def disable_tracing() -> None:
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["LANGCHAIN_TRACING_V2"] = "false"


def package_version() -> str:
    return version("sortingcap")


def main(argv: Sequence[str] | None = None) -> int:
    disable_tracing()
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
