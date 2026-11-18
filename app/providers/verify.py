"""Shared provider verification: one chat reply plus one tool-call round trip.

The prompts and the probe tool's description are the `verify` agent's prompt files.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.messages import message_text, short_error
from app.providers.base import SetupAborted

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

    from app.tools.context import ToolContext

_NO_TOOLS_HINTS = (
    "does not support tools",
    "tool use is not supported",
    "tools are not supported",
    "function calling is not supported",
    "not support function",
)


def verify_chat_model(ctx: ToolContext, model: BaseChatModel, provider_label: str) -> None:
    from langchain.agents import create_agent
    from langchain_core.messages import HumanMessage, ToolMessage
    from langchain_core.tools import StructuredTool

    try:
        reply = model.invoke([HumanMessage(ctx.texts.prompt("verify", "chat"))])
    except Exception as exc:  # noqa: BLE001 - any provider error is a verification failure
        raise SetupAborted(
            ctx.msg("verify.failed", provider=provider_label, detail=short_error(exc))
        ) from exc
    if not message_text(reply).strip():
        raise SetupAborted(ctx.msg("verify.empty_reply", provider=provider_label))

    calls: list[int] = []

    def ping(value: int) -> str:
        calls.append(value)
        return "pong"

    probe = StructuredTool.from_function(
        func=ping,
        name="ping",
        description=ctx.texts.prompt("verify", "tool_description").strip(),
    )
    try:
        agent = create_agent(model, tools=[probe])
        result = agent.invoke(
            {"messages": [HumanMessage(ctx.texts.prompt("verify", "tool"))]},
            config={"recursion_limit": 6},
        )
    except NotImplementedError as exc:
        raise SetupAborted(ctx.msg("verify.no_tools", provider=provider_label)) from exc
    except Exception as exc:  # noqa: BLE001
        if any(h in str(exc).lower() for h in _NO_TOOLS_HINTS):
            raise SetupAborted(ctx.msg("verify.no_tools", provider=provider_label)) from exc
        raise SetupAborted(
            ctx.msg("verify.failed", provider=provider_label, detail=short_error(exc))
        ) from exc
    used = any(isinstance(m, ToolMessage) and m.name == "ping" for m in result["messages"])
    if not (calls and used):
        raise SetupAborted(ctx.msg("verify.no_tool_call", provider=provider_label))
