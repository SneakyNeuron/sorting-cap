import enum
import inspect
from typing import Literal

import pytest
from pydantic import BaseModel, Field

from app.schemas.base import ToolInput, ToolOutput, positional
from app.texts import Texts
from app.tools import default_registry
from app.tools.registry import (
    ParseError,
    Registry,
    Tool,
    UnknownCommand,
    parse_command,
    split_chat_line,
)


class Color(enum.Enum):
    RED = "red"
    BLUE = "blue"


class DemoInput(ToolInput):
    target: str = Field(json_schema_extra=positional())
    name: str = "x"
    count: int = 1
    loud: bool = False
    mode: Literal["a", "b"] = "a"
    color: Color = Color.RED
    note: str | None = None


class DemoOutput(ToolOutput):
    echo: dict


def _demo_registry():
    reg = Registry()
    reg.register(
        Tool(
            "demo", DemoInput, DemoOutput, lambda ctx, i: DemoOutput(echo=i.model_dump(mode="json"))
        )
    )
    return reg


def test_duplicate_names_rejected():
    reg = default_registry()
    with pytest.raises(ValueError, match="duplicate"):
        reg.register(reg.get("help"))


def test_builtin_name_rejected(lenient_texts):
    with pytest.raises(ValueError, match="duplicate"):
        Registry().register(Tool("exit", DemoInput, DemoOutput, lambda c, i: None))


def test_missing_message_keys_rejected():
    with pytest.raises(ValueError, match="missing message keys") as exc:
        Registry().register(Tool("demo", DemoInput, DemoOutput, lambda c, i: None))
    assert "cs:tool.demo.description" in str(exc.value)
    assert "en:tool.demo.arg.count" in str(exc.value)


def test_argument_generation(lenient_texts):
    reg = _demo_registry()
    t = Texts("en")
    _, inputs = parse_command(reg, t, ["demo", "tgt"])
    assert inputs == DemoInput(target="tgt")
    _, inputs = parse_command(
        reg,
        t,
        [
            "demo",
            "tgt",
            "--name",
            "n",
            "--count",
            "3",
            "--loud",
            "--mode",
            "b",
            "--color",
            "blue",
            "--note",
            "hi",
        ],
    )
    assert inputs == DemoInput(
        target="tgt", name="n", count=3, loud=True, mode="b", color=Color.BLUE, note="hi"
    )
    _, inputs = parse_command(reg, t, ["demo", "tgt", "--no-loud"])
    assert inputs.loud is False


@pytest.mark.parametrize(
    "argv",
    [
        ["demo"],
        ["demo", "t", "--count", "many"],
        ["demo", "t", "--mode", "c"],
        ["demo", "t", "--bogus", "1"],
    ],
)
def test_bad_arguments(lenient_texts, argv):
    reg = _demo_registry()
    with pytest.raises(ParseError):
        parse_command(reg, Texts("en"), argv)


def test_unknown_command():
    with pytest.raises(UnknownCommand):
        parse_command(default_registry(), Texts("en"), ["nope"])


def test_split_chat_line():
    assert split_chat_line("/help settings") == ["help", "settings"]
    assert split_chat_line('/settings --model "a b"') == ["settings", "--model", "a b"]
    assert split_chat_line("//x") == ["/x"]  # only one leading slash is the prefix
    assert split_chat_line("-help") == ["-help"]  # the dash form is no command prefix


@pytest.mark.parametrize(
    ("lang", "typed", "expected"),
    [
        ("en", "setp", "Unknown command: /setp. Did you mean /setup? Try /help."),
        ("cs", "setp", "Neznámý příkaz: /setp. Nemyslíš /setup? Zkus /help."),
        ("en", "show-profil", "Did you mean /show-profile?"),
        ("en", "exti", "Did you mean /exit?"),
        ("en", "zzzzzz", "Unknown command: /zzzzzz. Try /help."),
        ("cs", "zzzzzz", "Neznámý příkaz: /zzzzzz. Zkus /help."),
    ],
)
def test_unknown_command_suggests_close_name(lang, typed, expected):
    with pytest.raises(UnknownCommand) as exc:
        parse_command(default_registry(), Texts(lang), [typed])
    assert expected in exc.value.text()
    assert exc.value.name == typed
    if typed == "zzzzzz":
        assert "/help?" not in exc.value.text() and "mean" not in exc.value.text()


def test_every_tool_has_keys_in_both_languages():
    for tool in default_registry().tools():
        for lang in ("cs", "en"):
            for key in tool.message_keys():
                assert Texts(lang).has(key), (lang, key)
    for lang in ("cs", "en"):
        assert Texts(lang).has("builtin.exit.description")


def test_models_are_pydantic_in_app_schemas():
    for tool in default_registry().tools():
        for model in (tool.input_model, tool.output_model):
            assert issubclass(model, BaseModel)
            assert inspect.getmodule(model).__name__.startswith("app.schemas."), model


def test_agent_and_chat_only_flags():
    reg = default_registry()
    assert [t.name for t in reg.agent_tools()] == ["help", "tour", "show-profile"]
    assert reg.get("setup").chat_only and not reg.get("setup").agent_callable
    assert not reg.get("settings").agent_callable


def test_same_input_from_cli_and_chat_line():
    reg = default_registry()
    t = Texts("en")
    cli = parse_command(reg, t, ["settings", "--model", "m"])
    chat = parse_command(reg, t, split_chat_line("/settings --model m"))
    assert cli == chat


class ListInput(ToolInput):
    source: list[str] = Field(default_factory=list)
    name: str = "x"


def _list_registry():
    reg = Registry()
    reg.register(
        Tool(
            "lister",
            ListInput,
            DemoOutput,
            lambda ctx, i: DemoOutput(echo=i.model_dump(mode="json")),
        )
    )
    return reg


def test_list_fields_are_repeatable_flags(lenient_texts):
    reg = _list_registry()
    t = Texts("en")
    _, inputs = parse_command(reg, t, ["lister"])
    assert inputs.source == []
    _, inputs = parse_command(reg, t, ["lister", "--source", "a.txt"])
    assert inputs.source == ["a.txt"]
    cli = parse_command(reg, t, ["lister", "--source", "a b.txt", "--source", "https://x.example/"])
    chat = parse_command(
        reg, t, split_chat_line('/lister --source "a b.txt" --source https://x.example/')
    )
    assert cli == chat
    assert cli[1].source == ["a b.txt", "https://x.example/"]


def test_list_field_flag_takes_a_value(lenient_texts):
    with pytest.raises(ParseError):
        parse_command(_list_registry(), Texts("en"), ["lister", "--source"])
