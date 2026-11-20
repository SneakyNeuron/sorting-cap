import io
import json
import threading
import time
from contextlib import contextmanager

import pytest
from helpers import console, fake_model
from langchain_core.messages import AIMessage
from prompt_toolkit.application import create_app_session
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from app.repl import Repl, SlashCompleter, TopMatchSuggest
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


def test_slash_command_dispatch(workspace, registry):
    code, out, _ = drive(workspace, "/stub --value 3\r/exit\r", registry=registry)
    assert code == 0
    assert [StubInput(value=3)] == SEEN


def test_same_tool_chat_line_and_runner(workspace, registry):
    drive(workspace, "/stub --action x --value 5\r/exit\r", registry=registry)
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
        workspace, "/nope\r/stub --bogus\r/stub --value 1\r/exit\r", registry=registry
    )
    assert "Unknown command: /nope" in out
    assert "Bad arguments for /stub" in out
    assert [StubInput(value=1)] == SEEN


def test_failing_and_crashing_tool_keep_running(workspace, registry):
    code, out, _ = drive(
        workspace, "/stub --action fail\r/stub --action crash\r/stub\r/exit\r", registry=registry
    )
    assert "stub failed" in out and "boom" in out
    assert len(SEEN) == 3


def test_exit_and_ctrl_d(workspace, registry):
    assert drive(workspace, "/exit\r", registry=registry)[0] == 0
    assert drive(workspace, "\x04", registry=registry)[0] == 0


def test_ctrl_c_cancels_line_without_quitting(workspace, registry):
    code, out, _ = drive(workspace, "half typed\x03/stub\r/exit\r", registry=registry)
    assert [StubInput()] == SEEN


def test_ctrl_c_cancels_running_tool(workspace, registry):
    code, out, _ = drive(workspace, "/stub --action interrupt\r/stub\r/exit\r", registry=registry)
    assert "Cancelled." in out
    assert len(SEEN) == 2


def test_interactive_confirm_in_chat(workspace, registry):
    code, out, _ = drive(workspace, "/stub --action confirm\ry\r/exit\r", registry=registry)
    assert "True" in out


def test_multiline_paste_is_one_input(workspace):
    model = fake_model("got it")
    drive(
        workspace,
        "\x1b[200~line one\nline two\x1b[201~\r/exit\r",
        settings=Settings(language="en", provider="ollama", model="m"),
        model=model,
    )
    human = [m for m in model.received[0] if m.type == "human"]
    assert human[-1].content == "line one\nline two"


def test_history_written(workspace, registry):
    drive(workspace, "/stub --value 9\r/exit\r", registry=registry)
    assert "/stub --value 9" in (workspace / ".history").read_text()


def test_history_file_is_0600(workspace, registry):
    import stat

    (workspace / ".history").write_text("")
    (workspace / ".history").chmod(0o644)
    drive(workspace, "/stub\r/exit\r", registry=registry)
    assert stat.S_IMODE((workspace / ".history").stat().st_mode) == 0o600


def test_open_workspace_warned_private_not(workspace, registry):
    workspace.chmod(0o755)
    _, out, repl = drive(workspace, "/exit\r", registry=registry)
    warning = repl.texts().message("repl.workspace_open", path=str(workspace))
    assert warning[:30] in out.replace("\n", " ")
    workspace.chmod(0o700)
    _, out, _ = drive(workspace, "/exit\r", registry=registry)
    assert warning[:30] not in out.replace("\n", " ")


def test_hidden_input_not_in_history(workspace, registry):
    drive(workspace, "/stub --action secret\rtop-secret-key\r/exit\r", registry=registry)
    assert "top-secret-key" not in (workspace / ".history").read_text()


def _completer(registry, lang="en"):
    from app.texts import Texts

    return SlashCompleter(registry, lambda: Texts(lang))


def _completions(completer, text):
    return list(completer.get_completions(Document(text), CompleteEvent()))


def _words(completer, text):
    return [c.text for c in _completions(completer, text)]


ALL = [
    "/help",
    "/tour",
    "/settings",
    "/setup",
    "/show-quarantine",
    "/release-quarantine",
    "/show-profile",
    "/add-information",
    "/stub",
    "/exit",
]


def test_completion_candidates(registry):
    completer = _completer(registry)
    assert _words(completer, "/") == ALL
    assert _words(completer, "/se") == ["/settings", "/setup"]
    assert _words(completer, "/sh") == ["/show-quarantine", "/show-profile"]
    assert _words(completer, "/zz") == []
    assert _words(completer, "/setup") == []  # a complete name needs no menu
    assert _words(completer, "hello") == []
    assert _words(completer, "-se") == []
    assert _words(completer, " /se") == []  # only `/` as the first character
    assert _words(completer, "say /se") == []
    assert all(c.start_position == -3 for c in _completions(completer, "/se"))


@pytest.mark.parametrize("lang", ["cs", "en"])
def test_completion_rows_show_name_and_description(lang):
    from app.texts import Texts

    completer = _completer(default_registry(), lang)
    rows = {c.text: (c.display_text, c.display_meta_text) for c in _completions(completer, "/")}
    texts = Texts(lang)
    assert rows["/settings"] == ("/settings", texts.message("tool.settings.description"))
    assert rows["/exit"] == ("/exit", texts.message("builtin.exit.description"))


def test_flag_candidates_after_a_command(registry):
    from app.texts import Texts

    completer = _completer(default_registry())
    flags = [
        "--language",
        "--provider",
        "--model",
        "--ollama-base-url",
        "--openai-base-url",
        "--guard-threshold",
    ]
    assert _words(completer, "/settings ") == flags
    assert _words(completer, "/settings --") == flags
    assert _words(completer, "/settings --o") == ["--ollama-base-url", "--openai-base-url"]
    meta = {c.text: c.display_meta_text for c in _completions(completer, "/settings --mo")}
    assert meta == {"--model": Texts("en").message("tool.settings.arg.model")}
    # flags already given are left out; a value is expected after a flag that takes one
    assert _words(completer, "/settings --language en ") == flags[1:]
    assert _words(completer, "/settings --model ") == []
    assert _words(completer, "/settings --model m --lang") == ["--language"]
    # repeatable (list-valued) flags stay offered
    assert _words(completer, "/add-information --source a.txt ") == ["--source"]
    # no flags for unknown commands, built-ins or flagless tools; no menu inside free text
    assert _words(completer, "/nope ") == []
    assert _words(completer, "/exit ") == []
    assert _words(completer, "/tour ") == []
    assert _words(completer, "hello --") == []


def test_help_argument_completes_command_names(registry):
    completer = _completer(registry)
    assert _words(completer, "/help to") == ["tour"]
    assert _words(completer, "/help sh") == ["show-quarantine", "show-profile"]
    assert _words(completer, "/help ") == [name[1:] for name in ALL]
    assert _words(completer, "/help tour ") == []


def test_ghost_text_is_rest_of_top_match(registry):
    from prompt_toolkit.buffer import Buffer

    suggest = TopMatchSuggest(_completer(registry))

    def ghost(text):
        found = suggest.get_suggestion(Buffer(), Document(text))
        return None if found is None else found.text

    assert ghost("/se") == "ttings "
    assert ghost("/") == "help "
    assert ghost("/settings --o") == "llama-base-url "
    assert ghost("/help to") == "ur "
    assert ghost("hello") is None
    assert ghost("/zz") is None
    assert ghost("/setup") is None


# --- keys, driven through prompt_toolkit's pipe input -----------------------------


def _stuff(ctx, inputs):
    SEEN.append(("stuff", inputs))
    return StubOutput(action="stuff", value=inputs.value)


def drive_keys(workspace, steps, states=None, timeout=10.0, **kw):
    """Send key chunks one at a time, each after the previous one has taken effect.

    `steps` are (chunk, condition) pairs. A chunk ending in Enter waits until the next
    prompt is up (or the REPL has ended); any other chunk waits until `condition(buffer)`
    holds, so the completion menu and ghost text are in place before the next key. No
    fixed sleeps: a slow machine only makes the waits longer, up to `timeout` each.
    `states`, when given, collects (menu open, ghost text) after each non-Enter chunk.
    """
    with repl_session(workspace, **kw) as (repl, inp, buf):
        session = repl.session
        buffer = session.default_buffer
        prompts = [0]
        prompt = session.prompt

        def counting_prompt(*args, **kwargs):
            prompts[0] += 1
            return prompt(*args, **kwargs)

        session.prompt = counting_prompt
        done = threading.Event()
        errors = []

        def wait(condition, what):
            deadline = time.monotonic() + timeout
            while not condition():
                if time.monotonic() > deadline:
                    raise AssertionError(f"timed out waiting for {what}")
                time.sleep(0.01)

        def prompt_up(n):
            return lambda: done.is_set() or (prompts[0] >= n and session.app.is_running)

        def feed():
            try:
                wait(prompt_up(1), "the first prompt")
                for chunk, condition in steps:
                    n = prompts[0]
                    inp.send_text(chunk)
                    if chunk.endswith("\r"):
                        wait(prompt_up(n + 1), f"the next prompt after {chunk!r}")
                        continue
                    wait(lambda c=condition: c(buffer), f"the effect of {chunk!r}")
                    if states is not None:
                        suggestion = buffer.suggestion.text if buffer.suggestion else None
                        states.append((buffer.complete_state is not None, suggestion))
            except BaseException as exc:  # re-raised in the test's thread below
                errors.append(exc)
            finally:
                inp.close()  # running out of input ends the REPL with EOF

        feeder = threading.Thread(target=feed)
        feeder.start()
        try:
            code = repl.run()
        finally:
            done.set()
            feeder.join()
        if errors:
            raise errors[0]
        return code, buf.getvalue(), repl


ESC = "\x1b"
DOWN = "\x1b[B"
RIGHT = "\x1b[C"
ENTER = "\r"


def menu(text, index=0):
    """The menu is open on `text` with row `index` highlighted and ghost text shown."""

    def condition(buffer):
        state = buffer.complete_state
        return (
            buffer.text == text
            and state is not None
            and state.complete_index == index
            and (index != 0 or buffer.suggestion is not None)
        )

    return condition


def closed(buffer):
    return buffer.complete_state is None and buffer.suggestion is None


def typed(text):
    return lambda buffer: buffer.text == text


@pytest.fixture
def two_stubs(registry):
    registry.register(Tool("stuff", StubInput, StubOutput, _stuff))
    return registry


def test_tab_accepts_highlighted_command(workspace, two_stubs):
    steps = [("/stu", menu("/stu")), ("\t", menu("/stub ")), ("--value 4\r", None)]
    drive_keys(workspace, [*steps, ("/exit\r", None)], registry=two_stubs)
    assert [StubInput(value=4)] == SEEN


def test_right_arrow_accepts_highlighted_command(workspace, two_stubs):
    steps = [("/stu", menu("/stu")), (RIGHT, menu("/stub ")), ("--value 5\r", None)]
    drive_keys(workspace, [*steps, ("/exit\r", None)], registry=two_stubs)
    assert [StubInput(value=5)] == SEEN


def test_down_then_enter_runs_second_match(workspace, two_stubs):
    steps = [("/stu", menu("/stu")), (DOWN, menu("/stuff", index=1)), (ENTER, None)]
    drive_keys(workspace, [*steps, ("/exit\r", None)], registry=two_stubs)
    assert [("stuff", StubInput())] == SEEN


def test_enter_runs_top_match_while_menu_open(workspace, two_stubs):
    steps = [("/stu", menu("/stu")), (ENTER, None), ("/exit\r", None)]
    drive_keys(workspace, steps, registry=two_stubs)
    assert [StubInput()] == SEEN


def test_esc_closes_menu_and_enter_submits_as_typed(workspace, two_stubs):
    states = []
    steps = [("/stu", menu("/stu")), (ESC, closed), (ENTER, None), ("/exit\r", None)]
    _, out, _ = drive_keys(workspace, steps, registry=two_stubs, states=states)
    assert states[0] == (True, "b ")  # menu open, ghost text for the top match
    assert states[1] == (False, None)  # Esc closed both
    assert SEEN == []
    assert "Unknown command: /stu. Did you mean /stub? Try /help." in out


def test_enter_in_flag_menu_submits_line_as_typed(workspace, two_stubs):
    steps = [("/stub ", menu("/stub ")), (ENTER, None), ("/exit\r", None)]
    drive_keys(workspace, steps, registry=two_stubs)
    assert [StubInput()] == SEEN


def test_enter_without_menu_submits_free_text(workspace, two_stubs):
    model = fake_model("hi")
    drive_keys(
        workspace,
        [("hello", typed("hello")), (ENTER, None), ("/exit\r", None)],
        registry=two_stubs,
        settings=Settings(language="en", provider="ollama", model="m"),
        model=model,
    )
    human = [m for m in model.received[0] if m.type == "human"]
    assert human[-1].content == "hello"
    assert SEEN == []


def test_session_uses_prompt_toolkit_menu_and_ghost_text(workspace, registry):
    from prompt_toolkit.shortcuts import CompleteStyle

    with repl_session(workspace, registry=registry) as (repl, inp, buf):
        session = repl.session
        assert session.default_buffer.complete_while_typing()
        assert session.complete_style == CompleteStyle.COLUMN
        assert isinstance(session.completer, SlashCompleter)
        assert isinstance(session.auto_suggest, TopMatchSuggest)


# --- prefix and unknown commands --------------------------------------------------


def test_dash_line_is_free_text_for_the_agent(workspace, registry):
    model = fake_model("noted")
    _, out, _ = drive(
        workspace,
        "-stub --value 3\r/exit\r",
        registry=registry,
        settings=Settings(language="en", provider="ollama", model="m"),
        model=model,
    )
    assert SEEN == []
    human = [m for m in model.received[0] if m.type == "human"]
    assert human[-1].content == "-stub --value 3"


@pytest.mark.parametrize(
    ("lang", "expected"),
    [
        ("en", "Unknown command: /stbu. Did you mean /stub? Try /help."),
        ("cs", "Neznámý příkaz: /stbu. Nemyslíš /stub? Zkus /help."),
    ],
)
def test_unknown_slash_word_never_reaches_agent(workspace, registry, lang, expected):
    model = fake_model("should not answer")
    _, out, _ = drive(
        workspace,
        "/stbu now\r/zzzzzz\r/exit\r",
        registry=registry,
        settings=Settings(language=lang, provider="ollama", model="m"),
        model=model,
    )
    assert expected in out
    assert ("/zzzzzz. Try /help." in out) or ("/zzzzzz. Zkus /help." in out)
    assert model.received == []
    assert SEEN == []


def test_first_start_language_prompt_saved_before_greeting(workspace):
    code, out, _ = drive(workspace, "2\r/exit\r", settings=None)
    assert load_settings(workspace).language == "en"
    assert "Jazyk / Language" in out
    assert out.index("Jazyk / Language") < out.index("The hat is on")


def test_setup_hint_until_setup_completes(workspace):
    _, out, _ = drive(workspace, "/exit\r")
    assert "Type /setup" in out
    from datetime import UTC, datetime

    _, out, _ = drive(
        workspace,
        "/exit\r",
        settings=Settings(
            language="en", provider="ollama", model="m", setup_completed_at=datetime.now(UTC)
        ),
    )
    assert "Type /setup" not in out


def test_free_text_without_model(workspace):
    _, out, _ = drive(workspace, "hello\r/exit\r")
    assert "Run /setup first" in out


def test_free_text_reaches_agent_and_markdown_answer(workspace):
    model = fake_model("**Hi** there")
    _, out, _ = drive(
        workspace,
        "hello\r/exit\r",
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
        "q\r/exit\r",
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
        "hi\r/help\r/exit\r",
        settings=Settings(language="en", provider="novita", model="m"),
        model=None,
    )
    with repl_session(
        workspace, settings=Settings(language="en", provider="novita", model="m")
    ) as (repl, inp, buf):
        repl.agent.model_factory = factory
        inp.send_text("hi\r/help\r/exit\r")
        inp.close()
        repl.run()
        text = buf.getvalue()
    assert "The model (novita) failed: no route to host" in text
    assert "/tour" in text  # /help still ran afterwards


def _raise_markup(ctx, inputs):
    raise ToolError("bad [/x] thing [bold]")


def test_markup_like_text_is_printed_literally(workspace, registry):
    registry.register(Tool("markup", StubInput, StubOutput, _raise_markup))
    code, out, repl = drive(
        workspace,
        "/foo[/x]\r/settings --provider zz[/q]\r/markup\r/stub --value 2\r/exit\r",
        registry=registry,
    )
    assert code == 0
    assert "Unknown command: /foo[/x]" in out
    assert "zz[/q]" in out
    assert "bad [/x] thing [bold]" in out
    assert [StubInput(value=2)] == SEEN


def test_markup_in_direct_calls_does_not_raise(workspace, registry):
    with repl_session(workspace, registry=registry) as (repl, inp, buf):
        assert repl.handle_line("/foo[/x]") is True
        assert repl.handle_line("/settings --provider zz[/q]") is True
        repl.error("[/red] closing")
        assert "[/red] closing" in buf.getvalue()


def test_validation_detail_is_not_swallowed(workspace, registry):
    _, out, _ = drive(
        workspace, "/settings --ollama-base-url localhost:1[x]\r/exit\r", registry=registry
    )
    assert "with a host, got 'localhost:1[x]'" in out.replace("\n", " ")


def test_ctrl_d_at_first_start_quits_cleanly(workspace):
    code, out, _ = drive(workspace, "\x04", settings=None)
    assert code == 0
    assert not (workspace / "settings.json").exists()
    assert "Jazyk / Language" in out


def test_ctrl_c_at_first_start_asks_again(workspace):
    code, out, _ = drive(workspace, "\x032\r/exit\r", settings=None)
    assert code == 0
    assert load_settings(workspace).language == "en"
    assert out.count("Jazyk / Language") == 2


def test_ctrl_d_at_tool_prompt_cancels(workspace, registry):
    code, out, _ = drive(workspace, "/stub --action confirm\r\x04/stub\r/exit\r", registry=registry)
    assert "Cancelled." in out and "crashed" not in out
    assert len(SEEN) == 2


def test_broken_settings_reported_in_default_language(workspace, registry):
    with repl_session(workspace, registry=registry) as (repl, inp, buf):
        (workspace / "settings.json").write_text("{oops")
        repl.handle_line("/stub")
        repl.handle_line("hello")
    text = buf.getvalue().replace("\n", " ")
    assert text.count("není platný JSON (řádek 1, sloupec 2)") == 2
    assert SEEN == []


def test_add_information_conversation_not_in_history(workspace, monkeypatch, lenient_texts):
    # Phase 3 R15 / V10 (validation fix D11): an intake message reaches the intake
    # agent, but neither the REPL's `.history` nor the chat agent's history
    from helpers import intake_model

    monkeypatch.setattr("app.intake.graph.needs_filter", lambda *_a, **_k: False)
    secret = "my-cat-is-called-QWERTY-PURR"
    model = intake_model("Hi! What roles are you after?", "Noted.")
    settings = Settings(language="en", provider="ollama", model="m")
    with repl_session(workspace, model=model, settings=settings) as (repl, inp, buf):
        inp.send_text(f"/add-information\rI like backend work, {secret}\rdone\r/exit\r")
        inp.close()
        assert repl.run() == 0
        chat_history = repl.agent.history
    assert any(secret in str(m.content) for call in model.received for m in call)
    assert secret not in (workspace / ".history").read_text()
    assert "/add-information" in (workspace / ".history").read_text()
    assert all(secret not in str(getattr(m, "content", m)) for m in chat_history)
    for path in workspace.rglob("*"):
        if path.is_file():
            assert secret.encode() not in path.read_bytes(), path
