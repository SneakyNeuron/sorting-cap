"""`-tour`: the app overview and tour."""

from __future__ import annotations

from app.schemas.tour import TourInput, TourOutput
from app.tools.context import ToolContext
from app.tools.registry import Tool


def tour_markdown(ctx: ToolContext) -> str:
    return (
        ctx.texts.prewritten("overview").rstrip()
        + "\n\n"
        + ctx.texts.prewritten("tour").rstrip()
        + "\n"
    )


def handle(ctx: ToolContext, inputs: TourInput) -> TourOutput:
    return TourOutput(markdown=tour_markdown(ctx))


TOOL = Tool(
    name="tour", input_model=TourInput, output_model=TourOutput, handler=handle, agent_callable=True
)
