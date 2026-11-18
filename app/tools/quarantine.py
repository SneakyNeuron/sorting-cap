"""`-show-quarantine [<id>]` (chat-only, user-only): list items, or show one decrypted (R6).

The text is decrypted in memory and printed by `render`; it is never written to disk,
the REPL history or any agent history.
"""

from __future__ import annotations

from app.guard.quarantine import Quarantine
from app.schemas.quarantine import ShowQuarantineInput, ShowQuarantineOutput
from app.tools.context import ToolContext
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
