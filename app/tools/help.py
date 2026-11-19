"""`/help`: list commands, or show one command's arguments."""

from __future__ import annotations

from app.schemas.help import ArgInfo, CommandDetail, CommandInfo, HelpInput, HelpOutput
from app.tools.context import ToolContext, ToolError
from app.tools.registry import BUILTINS, COMMAND_PREFIX, Tool, field_specs, unknown_command


def _builtin_description(ctx: ToolContext, name: str) -> str:
    return ctx.msg(f"builtin.{name}.description")


def handle(ctx: ToolContext, inputs: HelpInput) -> HelpOutput:
    registry = ctx.registry
    if inputs.command:
        name = inputs.command.removeprefix(COMMAND_PREFIX)
        if name in BUILTINS:
            return HelpOutput(
                command=CommandDetail(
                    name=name, description=_builtin_description(ctx, name), args=[]
                )
            )
        tool = registry.get(name)
        if tool is None:
            raise ToolError(unknown_command(registry, ctx.texts, name).message)
        args = [
            ArgInfo(
                name=spec.name if spec.positional else spec.flag,
                help=ctx.msg(tool.arg_help_key(spec.name)),
                choices=spec.choices,
                required=spec.required,
            )
            for spec in field_specs(tool)
        ]
        return HelpOutput(
            command=CommandDetail(name=name, description=ctx.msg(tool.description_key), args=args)
        )
    commands = [
        CommandInfo(name=t.name, description=ctx.msg(t.description_key)) for t in registry.tools()
    ]
    commands += [CommandInfo(name=b, description=_builtin_description(ctx, b)) for b in BUILTINS]
    return HelpOutput(commands=commands)


TOOL = Tool(
    name="help", input_model=HelpInput, output_model=HelpOutput, handler=handle, agent_callable=True
)
