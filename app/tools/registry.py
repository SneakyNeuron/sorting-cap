"""The one tool registry and the argparse parser generated from it.

The same parser serves `sortingcap run <command> ...` and slash commands typed
in chat, so CLI, cron and chat behave identically.
"""

from __future__ import annotations

import argparse
import enum
import types
import typing
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ValidationError
from pydantic.fields import FieldInfo

from app.errors import UserFacingError, validation_message
from app.schemas.base import ToolInput, ToolOutput
from app.texts import LANGUAGES, Texts, has_message
from app.tools.context import ToolContext, ToolError

BUILTINS = ("exit",)
COMMAND_PREFIX = "/"  # chat commands are `/name`; the headless runner takes bare names


@dataclass(frozen=True)
class Tool:
    name: str
    input_model: type[ToolInput]
    output_model: type[ToolOutput]
    handler: Callable[[ToolContext, Any], ToolOutput]
    agent_callable: bool = False
    chat_only: bool = False

    @property
    def description_key(self) -> str:
        return f"tool.{self.name}.description"

    def arg_help_key(self, field_name: str) -> str:
        return f"tool.{self.name}.arg.{field_name}"

    def message_keys(self) -> list[str]:
        return [self.description_key] + [
            self.arg_help_key(f) for f in self.input_model.model_fields
        ]

    def run(self, ctx: ToolContext, inputs: ToolInput) -> ToolOutput:
        output = self.handler(ctx, inputs)
        if not isinstance(output, self.output_model):
            raise TypeError(f"tool {self.name!r} returned {type(output).__name__}")
        return output


class Registry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> Tool:
        if tool.name in self._tools or tool.name in BUILTINS:
            raise ValueError(f"duplicate tool name: {tool.name!r}")
        missing = [
            f"{lang}:{key}"
            for lang in LANGUAGES
            for key in tool.message_keys()
            if not has_message(lang, key)
        ]
        if missing:
            raise ValueError(f"tool {tool.name!r} has missing message keys: {', '.join(missing)}")
        self._tools[tool.name] = tool
        return tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def tools(self) -> list[Tool]:
        return list(self._tools.values())

    def agent_tools(self) -> list[Tool]:
        return [t for t in self._tools.values() if t.agent_callable]

    def command_names(self) -> list[str]:
        return [*self._tools, *BUILTINS]


# --- parser generation -------------------------------------------------------


class ParseError(UserFacingError):
    """Bad command line; `message` is short and user-facing."""


class UnknownCommand(ParseError):
    def __init__(self, message: str, name: str) -> None:
        super().__init__(message)
        self.name = name


def unknown_command(registry: Registry, texts: Texts, name: str) -> UnknownCommand:
    """The localized "unknown command /x" error, with a close command name when one exists."""
    import difflib

    close = difflib.get_close_matches(name, registry.command_names(), n=1)
    if close:
        message = texts.message("parse.unknown_command_suggest", name=name, suggestion=close[0])
    else:
        message = texts.message("parse.unknown_command", name=name)
    return UnknownCommand(message, name)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> typing.NoReturn:
        raise ParseError(message)

    def exit(self, status: int = 0, message: str | None = None) -> typing.NoReturn:
        raise ParseError(message or "")


@dataclass(frozen=True)
class FieldSpec:
    name: str
    flag: str
    positional: bool
    # "str" | "bool" | "list". Other scalars (int, …) are passed as strings and coerced
    # by the input model, so a bad value gets the localized validation message. A
    # list-valued field is a repeatable flag (`--source a --source b`).
    kind: str
    choices: list[str] | None
    required: bool


def _unwrap_optional(annotation: Any) -> Any:
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return args[0]
    return annotation


def field_spec(name: str, info: FieldInfo) -> FieldSpec:
    extra = info.json_schema_extra if isinstance(info.json_schema_extra, dict) else {}
    annotation = _unwrap_optional(info.annotation)
    choices = None
    kind = "str"
    if typing.get_origin(annotation) is Literal:
        choices = [str(a) for a in typing.get_args(annotation)]
    elif isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        choices = [str(m.value) for m in annotation]
    elif annotation is bool:
        kind = "bool"
    elif typing.get_origin(annotation) in (list, tuple, set, frozenset):
        kind = "list"
    return FieldSpec(
        name=name,
        flag="--" + name.replace("_", "-"),
        positional=bool(extra.get("positional")),
        kind=kind,
        choices=choices,
        required=info.is_required(),
    )


def field_specs(tool: Tool) -> list[FieldSpec]:
    return [field_spec(n, i) for n, i in tool.input_model.model_fields.items()]


def build_parser(registry: Registry, texts: Texts) -> argparse.ArgumentParser:
    parser = _Parser(prog="sortingcap run", add_help=False)
    sub = parser.add_subparsers(dest="_tool", parser_class=_Parser)
    for tool in registry.tools():
        p = sub.add_parser(tool.name, add_help=False, help=texts.message(tool.description_key))
        for spec in field_specs(tool):
            help_text = texts.message(tool.arg_help_key(spec.name))
            kwargs: dict[str, Any] = {"help": help_text, "default": argparse.SUPPRESS}
            if spec.choices:
                kwargs["choices"] = spec.choices
            if spec.positional:
                if not spec.required:
                    kwargs["nargs"] = "?"
                p.add_argument(spec.name, **kwargs)
            elif spec.kind == "bool":
                p.add_argument(
                    spec.flag, dest=spec.name, action=argparse.BooleanOptionalAction, **kwargs
                )
            elif spec.kind == "list":
                p.add_argument(spec.flag, dest=spec.name, action="append", **kwargs)
            else:
                p.add_argument(spec.flag, dest=spec.name, **kwargs)
    return parser


def parse_command(registry: Registry, texts: Texts, argv: Sequence[str]) -> tuple[Tool, ToolInput]:
    """Parse `[command, *args]` into a tool and its validated input model."""
    if not argv:
        raise ParseError(texts.message("parse.no_command"))
    name = argv[0]
    tool = registry.get(name)
    if tool is None:
        raise unknown_command(registry, texts, name)
    parser = build_parser(registry, texts)
    try:
        namespace = parser.parse_args(list(argv))
    except ParseError as exc:
        raise ParseError(
            texts.message("parse.bad_arguments", name=name, detail=exc.message.strip())
        ) from exc
    values = {k: v for k, v in vars(namespace).items() if k != "_tool"}
    try:
        inputs = tool.input_model.model_validate(values)
    except ValidationError as exc:
        raise ParseError(
            texts.message("parse.bad_arguments", name=name, detail=validation_message(exc, texts))
        ) from exc
    return tool, inputs


def split_chat_line(line: str) -> list[str]:
    """`/help settings` → `["help", "settings"]` (shlex rules; one leading `/` removed)."""
    import shlex

    tokens = shlex.split(line)
    if tokens and tokens[0].startswith(COMMAND_PREFIX):
        tokens[0] = tokens[0].removeprefix(COMMAND_PREFIX)
    return tokens


__all__ = [
    "BUILTINS",
    "COMMAND_PREFIX",
    "ParseError",
    "Registry",
    "Tool",
    "ToolError",
    "UnknownCommand",
    "build_parser",
    "field_specs",
    "parse_command",
    "split_chat_line",
    "unknown_command",
]
