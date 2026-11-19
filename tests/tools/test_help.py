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
        assert f"/{name}" in text
    assert ctx.msg("tool.settings.description") in text


@pytest.mark.parametrize("lang", ["cs", "en"])
def test_help_listing_one_line_per_command_truncated(workspace, lang):
    save_settings(workspace, Settings(language=lang))
    ctx, buf = make_ctx(workspace)
    ctx.console.width = 40
    out = handle(ctx, HelpInput())
    out.render(ctx)
    lines = buf.getvalue().splitlines()
    rows = lines[: len(out.commands)]
    widest = max(len(c.name) for c in out.commands) + 1
    for cmd, row in zip(out.commands, rows, strict=True):
        assert row.startswith(f"/{cmd.name} ")  # one line each, in order
        assert len(row) <= 40
        # descriptions start in one aligned column, two spaces after the widest name
        assert row[len(cmd.name) + 1 : widest + 2].strip() == ""
        assert row[widest + 2] != " "
        assert cmd.description.startswith(row[widest + 2 :].rstrip().rstrip("…"))
    truncated = [r for r in rows if r.rstrip().endswith("…")]
    assert truncated  # long descriptions are cut with an ellipsis at 40 columns
    footer = lines[len(out.commands) :]
    assert ctx.msg("help.footer")[:20] in " ".join(footer)


def test_help_listing_wide_terminal_shows_full_descriptions(workspace):
    save_settings(workspace, Settings(language="en"))
    ctx, buf = make_ctx(workspace)
    ctx.console.width = 200
    out = handle(ctx, HelpInput())
    out.render(ctx)
    for cmd in out.commands:
        assert cmd.description in buf.getvalue()
    assert "…" not in buf.getvalue()


def test_help_detail_header_uses_slash(workspace):
    save_settings(workspace, Settings(language="en"))
    ctx, buf = make_ctx(workspace)
    handle(ctx, HelpInput(command="/settings")).render(ctx)
    assert buf.getvalue().startswith("/settings  ")


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
        "--guard-threshold",
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
