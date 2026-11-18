from __future__ import annotations

from app.schemas.base import ToolInput, ToolOutput


class TourInput(ToolInput):
    pass


class TourOutput(ToolOutput):
    markdown: str

    def render(self, ctx) -> None:
        from rich.markdown import Markdown

        ctx.console.print(Markdown(self.markdown))

    def as_agent_text(self) -> str:
        return self.markdown
