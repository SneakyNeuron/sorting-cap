"""`sortingcap run <command> [args] [--json] [--yes]`: run a tool without the chat.

Exit codes: 0 success, 1 tool failure or incomplete run, 2 bad arguments or a
chat-only tool.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from typing import TextIO

from rich.console import Console

from app.providers.base import SetupAborted
from app.settings import SettingsError, load_settings
from app.texts import Texts
from app.tools.context import HeadlessPrompter, ToolContext, ToolError
from app.tools.registry import ParseError, Registry, parse_command
from app.workspace import WorkspaceError, ensure_workspace

EXIT_OK, EXIT_FAILURE, EXIT_USAGE = 0, 1, 2
RUNNER_FLAGS = ("--json", "--yes")


def run_headless(
    argv: Sequence[str],
    *,
    registry: Registry | None = None,
    env: Mapping[str, str] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    **ctx_kwargs,
) -> int:
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    err = Console(file=stderr, highlight=False, soft_wrap=True)
    args = [a for a in argv if a not in RUNNER_FLAGS]
    as_json = "--json" in argv
    yes = "--yes" in argv
    try:
        workspace = ensure_workspace(env)
        settings = load_settings(workspace)
    except (WorkspaceError, SettingsError) as exc:
        err.print(f"error: {exc}")
        return EXIT_FAILURE
    texts = Texts(settings.language)
    if registry is None:
        from app.tools import default_registry

        registry = default_registry()
    try:
        tool, inputs = parse_command(registry, texts, args)
    except ParseError as exc:
        err.print(exc.message)
        return EXIT_USAGE
    if tool.chat_only:
        err.print(texts.message("headless.chat_only", name=tool.name))
        return EXIT_USAGE
    out = Console(file=stdout, highlight=False, soft_wrap=True)
    ctx = ToolContext(workspace=workspace, console=out, registry=registry, env=env, **ctx_kwargs)
    ctx.prompter = HeadlessPrompter(lambda: ctx.texts, yes=yes)
    if as_json:
        ctx.console = err  # progress and notices go to stderr; stdout carries only JSON
    try:
        output = tool.run(ctx, inputs)
    except (ToolError, SetupAborted) as exc:
        err.print(exc.message)
        return EXIT_FAILURE
    except SettingsError as exc:
        err.print(f"error: {exc}")
        return EXIT_FAILURE
    if as_json:
        stdout.write(output.model_dump_json() + "\n")
    else:
        output.render(ctx)
    return EXIT_OK
