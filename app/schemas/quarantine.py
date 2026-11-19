"""`/show-quarantine` and `/release-quarantine` input and output (R6, R7)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import Field

from app.messages import escape_controls, say
from app.schemas.base import ToolInput, ToolOutput, positional
from app.schemas.guard import QuarantineReport
from app.schemas.sources import SourceResult

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

        if self.item is None:
            if not self.items:
                say(ctx.console, ctx.msg("quarantine.empty"))
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
            say(ctx.console, f"{ctx.msg(f'quarantine.column.{label}')}: {value}")
        say(ctx.console, ctx.msg("quarantine.untrusted"), "yellow")
        say(ctx.console, ctx.msg("quarantine.text_begin"), "dim")
        say(ctx.console, self.text or "")
        say(ctx.console, ctx.msg("quarantine.text_end"), "dim")


class ReleaseQuarantineInput(ToolInput):
    id: str = Field(json_schema_extra=positional())


class ReleaseQuarantineOutput(ToolOutput):
    """The outcome of one release (R7); metadata only, never the content.

    `released`: ingested as a source (or already stored with the same content), the
    report marked `released` and `content.enc` deleted. `declined`: the user said no,
    nothing changed. `failed`: the source could not be stored (e.g. no text could be
    extracted); the item stays quarantined and the run is incomplete.
    """

    status: Literal["released", "declined", "failed"]
    item: QuarantineReport
    source: SourceResult | None = None

    def as_agent_text(self) -> str:  # never agent-callable; kept metadata-only anyway
        return self.model_dump_json()

    def render(self, ctx: ToolContext) -> None:
        from rich.text import Text

        params: dict[str, str] = {"id": escape_controls(self.item.id)}
        if self.source is not None:
            if self.source.slug:
                from app.intake.sources import sources_dir

                params["path"] = escape_controls(str(sources_dir(ctx.workspace) / self.source.slug))
            params["reason"] = escape_controls(self.source.reason or "")
        key = f"release_quarantine.{self.status}"
        if self.status == "released" and self.source and self.source.status == "skipped_duplicate":
            key = "release_quarantine.released_duplicate"
        style = {"released": "green", "declined": None, "failed": "red"}[self.status]
        ctx.console.print(Text(ctx.msg(key, **params), style=style or ""), highlight=False)
