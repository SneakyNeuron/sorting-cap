from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.base import ToolInput, ToolOutput, positional


class HelpInput(ToolInput):
    command: str | None = Field(default=None, json_schema_extra=positional())


class CommandInfo(BaseModel):
    name: str
    description: str


class ArgInfo(BaseModel):
    name: str
    help: str
    choices: list[str] | None = None
    required: bool = False


class CommandDetail(CommandInfo):
    args: list[ArgInfo]


class HelpOutput(ToolOutput):
    commands: list[CommandInfo] = []
    command: CommandDetail | None = None

    def render(self, ctx) -> None:
        from rich.markup import escape
        from rich.table import Table
        from rich.text import Text

        if self.command is not None:
            ctx.console.print(
                f"[bold]/{escape(self.command.name)}[/bold]  {escape(self.command.description)}"
            )
            if not self.command.args:
                ctx.console.print(ctx.texts.message("help.no_args"), markup=False)
                return
            table = Table(show_header=False, box=None, padding=(0, 2))
            for arg in self.command.args:
                extra = f" [dim]({escape(' | '.join(arg.choices))})[/dim]" if arg.choices else ""
                table.add_row(f"[cyan]{escape(arg.name)}[/cyan]", escape(arg.help) + extra)
            ctx.console.print(table)
            return
        # One line per command: `/name`, then its description cut with "…" to the width.
        names = [f"/{cmd.name}" for cmd in self.commands]
        column = max(map(len, names), default=0) + 2
        for name, cmd in zip(names, self.commands, strict=True):
            line = Text()
            line.append(name, style="cyan")
            line.append(" " * (column - len(name)) + (cmd.description.splitlines() or [""])[0])
            line.truncate(ctx.console.width, overflow="ellipsis")
            ctx.console.print(line, no_wrap=True, overflow="ellipsis")
        ctx.console.print(ctx.texts.message("help.footer"), style="dim", markup=False)
