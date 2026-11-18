"""Shared provider verification: one chat reply plus one tool-call round trip."""

from __future__ import annotations

from typing import TYPE_CHECKING

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


def short_error(exc: BaseException, limit: int = 200) -> str:
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    return text if len(text) <= limit else text[: limit - 1] + "…"


def message_text(message) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "".join(parts)


def verify_chat_model(ctx: ToolContext, model: BaseChatModel, provider_label: str) -> None:
    from langchain.agents import create_agent
    from langchain_core.messages import HumanMessage, ToolMessage
    from langchain_core.tools import tool

    try:
        reply = model.invoke([HumanMessage(ctx.texts.prompt("verify", "chat"))])
    except Exception as exc:  # noqa: BLE001 - any provider error is a verification failure
        raise SetupAborted(
            ctx.msg("verify.failed", provider=provider_label, detail=short_error(exc))
        ) from exc
    if not message_text(reply).strip():
        raise SetupAborted(ctx.msg("verify.empty_reply", provider=provider_label))

    calls: list[int] = []

    @tool
    def ping(value: int) -> str:
        """Verification probe. Call it with the number you were given."""
        calls.append(value)
        return "pong"

    try:
        agent = create_agent(model, tools=[ping])
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
