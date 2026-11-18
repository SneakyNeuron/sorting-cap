from __future__ import annotations

from app.schemas.base import ToolInput, ToolOutput


class SetupInput(ToolInput):
    pass


class SetupOutput(ToolOutput):
    completed: bool
    provider: str | None = None
    model: str | None = None

    def render(self, ctx) -> None:
        if self.completed:
            ctx.console.print(
                ctx.texts.message("setup.done", provider=self.provider, model=self.model),
                style="green",
                highlight=False,
                markup=False,
            )
        else:
            ctx.console.print(ctx.texts.message("setup.incomplete"), highlight=False, markup=False)
