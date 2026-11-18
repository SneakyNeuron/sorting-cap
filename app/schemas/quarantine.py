"""`-show-quarantine` input and output (R6)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import Field

from app.schemas.base import ToolInput, ToolOutput, positional
from app.schemas.guard import QuarantineReport

if TYPE_CHECKING:
    from app.tools.context import ToolContext


class ShowQuarantineInput(ToolInput):
    id: str | None = Field(default=None, json_schema_extra=positional())


def reason_text(ctx: ToolContext, report: QuarantineReport) -> str:
    parts = []
    if report.matched_rules:
        parts.append(", ".join(report.matched_rules))
    if report.max_score is not None:
        parts.append(ctx.msg("quarantine.reason_score", score=f"{report.max_score:.2f}"))
    return "; ".join(parts)


def _time(value) -> str:
    return value.astimezone().strftime("%Y-%m-%d %H:%M") if value else "—"


class ShowQuarantineOutput(ToolOutput):
    """A list of reports, or one report with its decrypted text.

    `text` is excluded from serialization and `repr`, so the decrypted content never
    reaches JSON output, an agent or a log through this model; only `render` prints it.
    """

    items: list[QuarantineReport] = []
    item: QuarantineReport | None = None
    text: str | None = Field(default=None, exclude=True, repr=False)

    def as_agent_text(self) -> str:  # never agent-callable; kept metadata-only anyway
        return self.model_dump_json()

    def render(self, ctx: ToolContext) -> None:
        from rich.table import Table
        from rich.text import Text

        from app.guard.quarantine import escape_controls

        def say(text: str, style: str | None = None) -> None:
            ctx.console.print(Text(text, style=style or ""), highlight=False)

        if self.item is None:
            if not self.items:
                say(ctx.msg("quarantine.empty"))
                return
            table = Table(box=None, padding=(0, 2))
            for column in ("id", "time", "source", "reason", "status"):
                table.add_column(Text(ctx.msg(f"quarantine.column.{column}")), style=None)
            for r in self.items:
                table.add_row(
                    Text(escape_controls(r.id)),
                    Text(_time(r.created_at)),
                    Text(escape_controls(r.source)),
                    Text(escape_controls(reason_text(ctx, r))),
                    Text(ctx.msg(f"quarantine.status.{r.status}")),
                )
            ctx.console.print(table)
            return
        r = self.item
        for label, value in (
            ("id", r.id),
            ("time", _time(r.created_at)),
            ("source", r.source),
            ("reason", reason_text(ctx, r)),
            ("status", ctx.msg(f"quarantine.status.{r.status}")),
        ):
            say(f"{ctx.msg(f'quarantine.column.{label}')}: {escape_controls(value)}")
        say(ctx.msg("quarantine.untrusted"), "yellow")
        say(ctx.msg("quarantine.text_begin"), "dim")
        ctx.console.print(Text(escape_controls(self.text or "")), highlight=False)
        say(ctx.msg("quarantine.text_end"), "dim")
