"""`sortingcap run <command> [args] [--json] [--yes]`: run a tool without the chat.

Exit codes: 0 success, 1 tool failure or incomplete run, 2 bad arguments or a
chat-only tool.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from typing import TextIO

from rich.console import Console

from app.errors import UserFacingError
from app.settings import load_settings
from app.texts import Texts
from app.tools.context import HeadlessPrompter, ToolContext
from app.tools.registry import ParseError, Registry, field_specs, parse_command
from app.workspace import ensure_workspace

EXIT_OK, EXIT_FAILURE, EXIT_USAGE = 0, 1, 2
RUNNER_FLAGS = ("--json", "--yes")


def split_runner_flags(registry: Registry, argv: Sequence[str]) -> tuple[list[str], set[str]]:
    """Take `--json`/`--yes` out of `argv`, except where they are another flag's value.

    A token right after one of the tool's value-taking flags (or an abbreviation
    of one, as argparse accepts) belongs to that flag, so `settings --model --yes`
    keeps `--yes` for the parser, which then rejects the missing value.
    """
    args: list[str] = []
    flags: set[str] = set()
    value_flags: list[str] = []
    takes_value = False
    for token in argv:
        if takes_value:
            args.append(token)
            takes_value = False
            continue
        if token in RUNNER_FLAGS:
            flags.add(token)
            continue
        args.append(token)
        if len(args) == 1:
            tool = registry.get(token)
            specs = field_specs(tool) if tool else []
            value_flags = [f.flag for f in specs if not f.positional and f.kind != "bool"]
        elif (
            token.startswith("--")
            and len(token) > 2
            and "=" not in token
            and any(flag.startswith(token) for flag in value_flags)
        ):
            takes_value = True
    return args, flags


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

    def report(text: str) -> None:
        err.print(text, highlight=False, markup=False)

    try:
        workspace = ensure_workspace(env)
        settings = load_settings(workspace)
    except UserFacingError as exc:  # no readable settings: default language
        report(exc.text())
        return EXIT_FAILURE
    texts = Texts(settings.language)
    if registry is None:
        from app.tools import default_registry

        registry = default_registry()
    args, flags = split_runner_flags(registry, argv)
    as_json = "--json" in flags
    yes = "--yes" in flags
    tool = registry.get(args[0]) if args else None
    if tool is not None and tool.chat_only:  # refused before its arguments are parsed
        report(texts.message("headless.chat_only", name=tool.name))
        return EXIT_USAGE
    try:
        tool, inputs = parse_command(registry, texts, args)
    except ParseError as exc:
        report(exc.text(texts))
        return EXIT_USAGE
    out = Console(file=stdout, highlight=False, soft_wrap=True)
    try:
        ctx = ToolContext(
            workspace=workspace, console=out, registry=registry, env=env, **ctx_kwargs
        )
        ctx.prompter = HeadlessPrompter(lambda: ctx.texts, yes=yes)
        if as_json:
            ctx.console = err  # progress and notices go to stderr; stdout carries only JSON
        output = tool.run(ctx, inputs)
    except UserFacingError as exc:
        report(exc.text(texts))
        return EXIT_FAILURE
    if as_json:
        stdout.write(output.model_dump_json() + "\n")
    else:
        output.render(ctx)
    return EXIT_FAILURE if output.incomplete else EXIT_OK
