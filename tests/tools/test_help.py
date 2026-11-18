import pytest
from helpers import make_ctx

from app.schemas.help import HelpInput
from app.settings import Settings, save_settings
from app.tools.help import handle


@pytest.mark.parametrize("lang", ["cs", "en"])
def test_help_lists_all_commands_and_exit(workspace, lang):
    save_settings(workspace, Settings(language=lang))
    ctx, buf = make_ctx(workspace)
    out = handle(ctx, HelpInput())
    names = [c.name for c in out.commands]
    assert names == [*[t.name for t in ctx.registry.tools()], "exit"]
    out.render(ctx)
    text = buf.getvalue()
    for name in names:
        assert f"-{name}" in text
    assert ctx.msg("tool.settings.description") in text


@pytest.mark.parametrize("lang", ["cs", "en"])
def test_help_command_shows_flags(workspace, lang):
    save_settings(workspace, Settings(language=lang))
    ctx, buf = make_ctx(workspace)
    out = handle(ctx, HelpInput(command="settings"))
    flags = [a.name for a in out.command.args]
    assert flags == [
        "--language",
        "--provider",
        "--model",
        "--ollama-base-url",
        "--openai-base-url",
    ]
    out.render(ctx)
    assert "--openai-base-url" in buf.getvalue()
    assert ctx.msg("tool.settings.arg.language") in buf.getvalue()


def test_help_positional_via_chat_line_and_runner(workspace, capsys):
    from app.tools.headless import run_headless

    assert run_headless(["help", "settings"]) == 0
    assert "--ollama-base-url" in capsys.readouterr().out


def test_help_unknown_command(workspace):
    from app.tools.context import ToolError

    ctx, _ = make_ctx(workspace)
    with pytest.raises(ToolError):
        handle(ctx, HelpInput(command="nope"))
