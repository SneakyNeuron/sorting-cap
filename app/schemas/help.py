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
        from rich.table import Table

        if self.command is not None:
            ctx.console.print(f"[bold]-{self.command.name}[/bold]  {self.command.description}")
            if not self.command.args:
                ctx.console.print(ctx.texts.message("help.no_args"))
                return
            table = Table(show_header=False, box=None, padding=(0, 2))
            for arg in self.command.args:
                extra = f" [dim]({' | '.join(arg.choices)})[/dim]" if arg.choices else ""
                table.add_row(f"[cyan]{arg.name}[/cyan]", arg.help + extra)
            ctx.console.print(table)
            return
        table = Table(show_header=False, box=None, padding=(0, 2))
        for cmd in self.commands:
            table.add_row(f"[cyan]-{cmd.name}[/cyan]", cmd.description)
        ctx.console.print(table)
        ctx.console.print(f"[dim]{ctx.texts.message('help.footer')}[/dim]")
