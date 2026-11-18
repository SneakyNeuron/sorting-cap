import io
import json

import pytest
from helpers import FakeRunner

from app.schemas.base import ToolInput, ToolOutput
from app.tools import default_registry
from app.tools.context import ToolError
from app.tools.headless import run_headless
from app.tools.registry import Tool


class StubInput(ToolInput):
    action: str = "confirm"


class StubOutput(ToolOutput):
    answer: str


def _handler(ctx, inputs):
    if inputs.action == "confirm":
        return StubOutput(answer=str(ctx.confirm("ok?")))
    if inputs.action == "fail":
        raise ToolError("it broke")
    if inputs.action == "markup":
        raise ToolError("it [/x] broke")
    if inputs.action == "choose":
        return StubOutput(answer=ctx.choose("pick?", []))
    if inputs.action == "text":
        return StubOutput(answer=ctx.text("name?"))
    return StubOutput(answer=ctx.secret("key?"))


@pytest.fixture
def registry(lenient_texts):
    reg = default_registry()
    reg.register(Tool("stub", StubInput, StubOutput, _handler))
    reg.register(Tool("chatty", StubInput, StubOutput, _handler, chat_only=True))
    return reg


def run(argv, registry):
    out, err = io.StringIO(), io.StringIO()
    code = run_headless(
        argv,
        registry=registry,
        env=None,
        stdout=out,
        stderr=err,
        runner_factory=lambda c: FakeRunner(),
    )
    return code, out.getvalue(), err.getvalue()


def test_success_and_json_parses_back(registry):
    code, out, _ = run(["stub", "--json"], registry)
    assert code == 0
    assert StubOutput.model_validate(json.loads(out)) == StubOutput(answer="False")


def test_confirm_denied_without_yes_and_granted_with(registry):
    assert json.loads(run(["stub", "--json"], registry)[1])["answer"] == "False"
    assert json.loads(run(["stub", "--json", "--yes"], registry)[1])["answer"] == "True"


@pytest.mark.parametrize("action", ["choose", "text", "secret"])
def test_inputs_fail_headless(registry, action):
    code, out, err = run(["stub", "--action", action], registry)
    assert code == 1
    assert "?" in err


def test_tool_failure_exit_one(registry):
    code, _, err = run(["stub", "--action", "fail"], registry)
    assert (code, err.strip()) == (1, "it broke")


def test_bad_arguments_exit_two(registry):
    assert run(["stub", "--nope"], registry)[0] == 2
    assert run(["nope"], registry)[0] == 2
    assert run([], registry)[0] == 2


def test_chat_only_refused_with_exit_two(registry):
    assert run(["chatty"], registry)[0] == 2
    assert run(["chatty", "--yes"], registry)[0] == 2


@pytest.mark.parametrize("extra", [["--bogus"], ["--action"], ["x", "y"]])
def test_chat_only_refused_before_arguments_are_parsed(registry, extra):
    code, _, err = run(["chatty", *extra], registry)
    assert code == 2
    assert "jen v chatu" in err and "-chatty" in err and "Špatné argumenty" not in err


def test_runner_flags_anywhere_except_as_a_flag_value(registry):
    assert json.loads(run(["--yes", "stub", "--json"], registry)[1])["answer"] == "True"
    assert json.loads(run(["stub", "--action=confirm", "--yes", "--json"], registry)[1]) == {
        "answer": "True",
        "incomplete": False,
    }
    # `--yes` right after a value-taking flag (or its abbreviation) is that flag's
    # value: the parser rejects it instead of the runner silently auto-confirming
    for argv in (["stub", "--action", "--yes"], ["stub", "--act", "--yes"]):
        code, out, err = run(argv, registry)
        assert (code, out) == (2, ""), argv
        assert "Špatné argumenty pro -stub" in err


def test_markup_like_error_text_printed_literally(registry):
    code, _, err = run(["stub", "--action", "markup"], registry)
    assert (code, err.strip()) == (1, "it [/x] broke")


def test_broken_settings_reported_in_default_language(registry, workspace):
    (workspace / "settings.json").write_text("{oops")
    code, _, err = run(["stub"], registry)
    assert code == 1
    assert "není platný JSON (řádek 1, sloupec 2)" in err and "error:" not in err


@pytest.mark.parametrize("extra", [[], ["--yes"], ["--json", "--yes"], ["--force"]])
def test_setup_refused_headless(extra):
    out, err = io.StringIO(), io.StringIO()
    code = run_headless(["setup", *extra], stdout=out, stderr=err)
    assert code == 2
    assert "-setup" in err.getvalue()


class IncompleteOutput(ToolOutput):
    answer: str = "partial"

    def render(self, ctx) -> None:
        ctx.console.print("summary printed", markup=False, highlight=False)


def test_incomplete_output_rendered_then_exit_one(lenient_texts):
    reg = default_registry()
    reg.register(
        Tool("partial", StubInput, IncompleteOutput, lambda c, i: IncompleteOutput(incomplete=True))
    )
    code, out, _ = run(["partial"], reg)
    assert code == 1
    assert "summary printed" in out
    code, out, _ = run(["partial", "--json"], reg)
    assert code == 1
    assert json.loads(out)["incomplete"] is True


def test_output_complete_by_default():
    assert StubOutput(answer="x").incomplete is False
