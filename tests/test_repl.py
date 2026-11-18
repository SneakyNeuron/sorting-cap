import io
import json
from contextlib import contextmanager

import pytest
from helpers import console, fake_model
from langchain_core.messages import AIMessage
from prompt_toolkit.application import create_app_session
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from app.repl import DashCompleter, Repl
from app.schemas.base import ToolInput, ToolOutput
from app.settings import Settings, load_settings, save_settings
from app.tools import default_registry
from app.tools.context import ToolError
from app.tools.headless import run_headless
from app.tools.registry import Tool


class StubInput(ToolInput):
    action: str = "ok"
    value: int = 0


class StubOutput(ToolOutput):
    action: str
    value: int


SEEN: list = []


def _stub(ctx, inputs):
    SEEN.append(inputs)
    if inputs.action == "fail":
        raise ToolError("stub failed")
    if inputs.action == "crash":
        raise RuntimeError("boom")
    if inputs.action == "interrupt":
        raise KeyboardInterrupt
    if inputs.action == "secret":
        ctx.secret("key?")
    if inputs.action == "confirm":
        return StubOutput(action=str(ctx.confirm("sure?")), value=inputs.value)
    return StubOutput(action=inputs.action, value=inputs.value)


@pytest.fixture
def registry(lenient_texts):
    SEEN.clear()
    reg = default_registry()
    reg.register(Tool("stub", StubInput, StubOutput, _stub))
    return reg


@contextmanager
def repl_session(workspace, registry=None, model=None, settings="default"):
    if settings == "default":
        settings = Settings(language="en")
    if settings is not None:
        save_settings(workspace, settings)
    con, buf = console()
    with create_pipe_input() as inp, create_app_session(input=inp, output=DummyOutput()):
        repl = Repl(workspace, con, registry, model_factory=lambda s, c: model)
        yield repl, inp, buf


def drive(workspace, keys, **kw):
    with repl_session(workspace, **kw) as (repl, inp, buf):
        inp.send_text(keys)
        inp.close()  # running out of input ends with EOF instead of hanging
        code = repl.run()
        return code, buf.getvalue(), repl


def test_dash_command_dispatch(workspace, registry):
    code, out, _ = drive(workspace, "-stub --value 3\r-exit\r", registry=registry)
    assert code == 0
    assert [StubInput(value=3)] == SEEN


def test_same_tool_chat_line_and_runner(workspace, registry):
    drive(workspace, "-stub --action x --value 5\r-exit\r", registry=registry)
    out = io.StringIO()
    assert (
        run_headless(
            ["stub", "--action", "x", "--value", "5", "--json"], registry=registry, stdout=out
        )
        == 0
    )
    assert SEEN[0] == SEEN[1]
    assert StubOutput.model_validate(json.loads(out.getvalue())) == StubOutput(action="x", value=5)


def test_unknown_command_and_bad_flag_keep_running(workspace, registry):
    code, out, _ = drive(
        workspace, "-nope\r-stub --bogus\r-stub --value 1\r-exit\r", registry=registry
    )
    assert "Unknown command: -nope" in out
    assert "Bad arguments for -stub" in out
    assert [StubInput(value=1)] == SEEN


def test_failing_and_crashing_tool_keep_running(workspace, registry):
    code, out, _ = drive(
        workspace, "-stub --action fail\r-stub --action crash\r-stub\r-exit\r", registry=registry
    )
    assert "stub failed" in out and "boom" in out
    assert len(SEEN) == 3


def test_exit_and_ctrl_d(workspace, registry):
    assert drive(workspace, "-exit\r", registry=registry)[0] == 0
    assert drive(workspace, "\x04", registry=registry)[0] == 0


def test_ctrl_c_cancels_line_without_quitting(workspace, registry):
    code, out, _ = drive(workspace, "half typed\x03-stub\r-exit\r", registry=registry)
    assert [StubInput()] == SEEN


def test_ctrl_c_cancels_running_tool(workspace, registry):
    code, out, _ = drive(workspace, "-stub --action interrupt\r-stub\r-exit\r", registry=registry)
    assert "Cancelled." in out
    assert len(SEEN) == 2


def test_interactive_confirm_in_chat(workspace, registry):
    code, out, _ = drive(workspace, "-stub --action confirm\ry\r-exit\r", registry=registry)
    assert "True" in out


def test_multiline_paste_is_one_input(workspace):
    model = fake_model("got it")
    drive(
        workspace,
        "\x1b[200~line one\nline two\x1b[201~\r-exit\r",
        settings=Settings(language="en", provider="ollama", model="m"),
        model=model,
    )
    human = [m for m in model.received[0] if m.type == "human"]
    assert human[-1].content == "line one\nline two"


def test_history_written(workspace, registry):
    drive(workspace, "-stub --value 9\r-exit\r", registry=registry)
    assert "-stub --value 9" in (workspace / ".history").read_text()


def test_hidden_input_not_in_history(workspace, registry):
    drive(workspace, "-stub --action secret\rtop-secret-key\r-exit\r", registry=registry)
    assert "top-secret-key" not in (workspace / ".history").read_text()


def test_completion_candidates(registry):
    completer = DashCompleter(registry.command_names)

    def words(text):
        return [c.text for c in completer.get_completions(Document(text), CompleteEvent())]  # noqa: E731

    assert set(words("-")) == {"-help", "-tour", "-settings", "-setup", "-stub", "-exit"}
    assert words("-se") == ["-settings", "-setup"]
    assert words("-help to") == ["tour"]
    assert words("hello") == []


def test_first_start_language_prompt_saved_before_greeting(workspace):
    code, out, _ = drive(workspace, "2\r-exit\r", settings=None)
    assert load_settings(workspace).language == "en"
    assert "Jazyk / Language" in out
    assert out.index("Jazyk / Language") < out.index("The hat is on")


def test_setup_hint_until_setup_completes(workspace):
    _, out, _ = drive(workspace, "-exit\r")
    assert "Type -setup" in out
    from datetime import UTC, datetime

    _, out, _ = drive(
        workspace,
        "-exit\r",
        settings=Settings(
            language="en", provider="ollama", model="m", setup_completed_at=datetime.now(UTC)
        ),
    )
    assert "Type -setup" not in out


def test_free_text_without_model(workspace):
    _, out, _ = drive(workspace, "hello\r-exit\r")
    assert "Run -setup first" in out


def test_free_text_reaches_agent_and_markdown_answer(workspace):
    model = fake_model("**Hi** there")
    _, out, _ = drive(
        workspace,
        "hello\r-exit\r",
        settings=Settings(language="en", provider="ollama", model="m"),
        model=model,
    )
    assert "Hi there" in out


def test_reasoning_shown_above_answer(workspace):
    model = fake_model(
        AIMessage("The answer", additional_kwargs={"reasoning_content": "pondering hats"})
    )
    _, out, _ = drive(
        workspace,
        "q\r-exit\r",
        settings=Settings(language="en", provider="ollama", model="m"),
        model=model,
    )
    assert out.index("pondering hats") < out.index("The answer")


def test_reasoning_is_dim(workspace):
    from rich.console import Console

    save_settings(workspace, Settings(language="en", provider="ollama", model="m"))
    buf = io.StringIO()
    con = Console(file=buf, force_terminal=True, color_system="standard", width=100)
    model = fake_model(AIMessage("Answer", additional_kwargs={"reasoning_content": "thinking"}))
    with create_pipe_input() as inp, create_app_session(input=inp, output=DummyOutput()):
        repl = Repl(workspace, con, model_factory=lambda s, c: model)
        repl.chat("q")
    assert "\x1b[2mthinking" in buf.getvalue()


def test_model_error_keeps_repl_alive(workspace):
    class Broken:
        def bind_tools(self, *a, **k):
            return self

    def factory(s, c):
        raise ConnectionError("no route to host")

    _, out, _ = drive(
        workspace,
        "hi\r-help\r-exit\r",
        settings=Settings(language="en", provider="novita", model="m"),
        model=None,
    )
    with repl_session(
        workspace, settings=Settings(language="en", provider="novita", model="m")
    ) as (repl, inp, buf):
        repl.agent.model_factory = factory
        inp.send_text("hi\r-help\r-exit\r")
        inp.close()
        repl.run()
        text = buf.getvalue()
    assert "The model (novita) failed: no route to host" in text
    assert "-tour" in text  # -help still ran afterwards
