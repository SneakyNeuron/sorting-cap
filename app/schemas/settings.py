from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.base import ToolInput, ToolOutput


class SettingsInput(ToolInput):
    language: Literal["cs", "en"] | None = None
    provider: str | None = None
    model: str | None = None
    ollama_base_url: str | None = None
    openai_base_url: str | None = None


class SettingsOutput(ToolOutput):
    settings: dict[str, object]
    keys: dict[str, str] = Field(description="Remote API keys, masked")
    changed: list[str] = []
    notes: list[str] = []

    def render(self, ctx) -> None:
        from rich.markup import escape
        from rich.table import Table

        table = Table(show_header=False, box=None, padding=(0, 2))
        for name, value in self.settings.items():
            mark = " [green]*[/green]" if name in self.changed else ""
            shown = "—" if value is None else escape(str(value))
            table.add_row(f"[cyan]{escape(name)}[/cyan]", shown + mark)
        for name, value in self.keys.items():
            table.add_row(f"[cyan]{escape(name)}[/cyan]", escape(value))
        ctx.console.print(table)
        for note in self.notes:
            ctx.console.print(note, style="yellow", highlight=False, markup=False)
