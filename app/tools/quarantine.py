"""`/show-quarantine [<id>]` and `/release-quarantine <id>` (chat-only, user-only; R6, R7).

`/show-quarantine` lists items, or decrypts one in memory and prints it through
`render`; the text is never written to disk, the REPL history or any agent history.

`/release-quarantine` shows the item the same way, asks (default no) and on yes
ingests the decrypted bytes as a profile source without guarding them again, then
marks the report `released` and deletes `content.enc`. It refuses to run under the
headless prompter even when the runner did not stop it first, so a `--yes` can never
release anything.
"""

from __future__ import annotations

from pathlib import Path

from app.guard.quarantine import Quarantine
from app.schemas.quarantine import (
    ReleaseQuarantineInput,
    ReleaseQuarantineOutput,
    ShowQuarantineInput,
    ShowQuarantineOutput,
)
from app.schemas.sources import SourceType
from app.tools.context import ToolContext, ToolError
from app.tools.registry import Tool


def store(ctx: ToolContext) -> Quarantine:
    return Quarantine(ctx.workspace, ctx.credentials)


def handle(ctx: ToolContext, inputs: ShowQuarantineInput) -> ShowQuarantineOutput:
    quarantine = store(ctx)
    if inputs.id is None:
        return ShowQuarantineOutput(items=quarantine.list())
    report = quarantine.report(inputs.id)
    raw = quarantine.decrypt(inputs.id)
    return ShowQuarantineOutput(item=report, text=raw.decode("utf-8", errors="replace"))


TOOL = Tool(
    name="show-quarantine",
    input_model=ShowQuarantineInput,
    output_model=ShowQuarantineOutput,
    handler=handle,
    chat_only=True,
)


# --- release (R7) ----------------------------------------------------------------

RELEASE_NAME = "release-quarantine"


def _source_kind(source: str) -> tuple[SourceType, str, str | None]:
    """Type, stored extension and final URL of a quarantined source.

    Only guarded input is ever quarantined: a URL (the report records the final URL)
    or an `.html`/`.htm` file. Any other recorded path is treated as HTML, since that
    is how the guard read it.
    """
    from app.intake.sources import is_url

    if is_url(source):
        return "url", ".html", source.strip()
    ext = Path(source).suffix.lower()
    return "html", ext if ext in (".html", ".htm") else ".html", None


def release(ctx: ToolContext, inputs: ReleaseQuarantineInput) -> ReleaseQuarantineOutput:
    if ctx.is_headless:
        raise ToolError(ctx.msg("headless.chat_only", name=RELEASE_NAME))
    from app.intake.sources import ingest_bytes

    quarantine = store(ctx)
    report = quarantine.report(inputs.id)
    raw = quarantine.decrypt(inputs.id)  # refuses a released item, a missing or wrong key
    ShowQuarantineOutput(item=report, text=raw.decode("utf-8", errors="replace")).render(ctx)
    if not ctx.confirm(ctx.msg("release_quarantine.confirm", id=report.id), default=False):
        return ReleaseQuarantineOutput(status="declined", item=report)
    kind, ext, final_url = _source_kind(report.source)
    result = ingest_bytes(
        ctx, raw, origin=report.source, kind=kind, ext=ext, final_url=final_url, guarded=False
    )
    if result.status not in ("added", "skipped_duplicate"):
        return ReleaseQuarantineOutput(status="failed", item=report, source=result, incomplete=True)
    released = quarantine.mark_released(report.id)
    return ReleaseQuarantineOutput(status="released", item=released, source=result)


RELEASE_TOOL = Tool(
    name=RELEASE_NAME,
    input_model=ReleaseQuarantineInput,
    output_model=ReleaseQuarantineOutput,
    handler=release,
    chat_only=True,
)
