"""Base classes for tool inputs and outputs."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from app.tools.context import ToolContext


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolOutput(BaseModel):
    # Set when the tool did part of its work (e.g. some sources refused); the headless
    # runner renders the output as usual and then exits 1.
    incomplete: bool = False

    def render(self, ctx: ToolContext) -> None:
        """Show the output to a human. Subclasses override this."""
        from rich.markup import escape
        from rich.table import Table

        table = Table(show_header=False, box=None)
        for name, value in self.model_dump(mode="json").items():
            table.add_row(escape(name), escape(str(value)))
        ctx.console.print(table)

    def as_agent_text(self) -> str:
        """What the chat agent receives as the tool result."""
        return self.model_dump_json()


def positional(**extra: object) -> dict[str, object]:
    """`json_schema_extra` marking a field as a positional argument."""
    return {"positional": True, **extra}
