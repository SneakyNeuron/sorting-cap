"""The intake agent: one bounded turn of the `/add-information` conversation (Phase 3
R1, R2, R8, R11, T4, T5).

The agent is the chat model from settings with `bind_tools` over exactly the five
in-round tools of `app/intake/round.py`; it never gets a registry tool and never sees
source text (only source reports, R7).

One turn: the model is called, its tool calls run in order and their results go back
as tool messages, until it replies with text and no tool call, calls `finish`, or the
turn reaches `MAX_TOOL_CALLS` (then the turn ends with the text it gave, or the
localized "go on" line; not a failure). A tool call with invalid arguments (or an
unknown tool) gets the validation error as its tool message; the model is called
again once, and a second invalid call fails the turn. A model error (after the
transient retries of `app/providers/structured.py`) or an empty reply also fails it.
Text from the model is printed as plain text, controls escaped; a failed turn prints
a short localized error and keeps everything recorded so far.

The model's input (T5): the `intake/agent` prompt, the round's history capped at about
`HISTORY_LIMIT` messages (cut only before a user message, so a tool message never
loses its call), then the context message (checklist status and new source reports,
as JSON), the user's message, and this turn's replies and tool messages.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from pydantic import ValidationError

from app.intake.round import TOOL_NAMES
from app.messages import message_text, say, short_error
from app.providers import structured
from app.providers.structured import TRANSIENT_ATTEMPTS, TRANSIENT_DELAYS, is_transient

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import BaseMessage
    from langchain_core.tools import BaseTool

    from app.intake.round import RoundContext
    from app.tools.context import ToolContext

MAX_TOOL_CALLS = 8
HISTORY_LIMIT = 40
ERROR_LIMIT = 1_000  # characters of a validation error fed back to the model

TurnKind = Literal["reply", "finished", "capped", "failed"]


@dataclass
class TurnOutcome:
    """One turn: how it ended, the text shown to the user, the error of a failed turn,
    and the messages to keep in the round's history (on failure only the user's)."""

    kind: TurnKind
    text: str = ""
    error: str | None = None
    messages: list[BaseMessage] = field(default_factory=list)
    tool_calls: int = 0

    @property
    def failed(self) -> bool:
        return self.kind == "failed"


def failed_turn(
    ctx: ToolContext, user_message: str | None, error: str, *, tool_calls: int = 0
) -> TurnOutcome:
    """A failed turn (R11): prints `intake.agent.failed` with `error`; of the turn's
    messages only the user's (when there is one) stays in the history."""
    from langchain_core.messages import HumanMessage

    say(ctx.console, ctx.msg("intake.agent.failed", detail=error), "yellow")
    kept: list[BaseMessage] = [HumanMessage(user_message)] if user_message is not None else []
    return TurnOutcome("failed", error=error, messages=kept, tool_calls=tool_calls)


def build_tools(round_: RoundContext) -> list[BaseTool]:
    """The five in-round tools as LangChain tools (for `bind_tools`; the turn runs the
    handlers itself so it can validate and count every call)."""
    from langchain_core.tools import StructuredTool

    tools: list[BaseTool] = []
    for name, (schema, handler) in round_.handlers().items():

        def _run(_handler=handler, _schema=schema, **kwargs: Any) -> str:
            return _handler(_schema.model_validate(kwargs)).model_dump_json()

        tools.append(
            StructuredTool.from_function(
                func=_run,
                name=name,
                description=round_.ctx.msg(f"intake.tool.{name}"),
                args_schema=schema,
            )
        )
    return tools


def bind(model: BaseChatModel, round_: RoundContext) -> Any:
    return model.bind_tools(build_tools(round_))


def cut_history(messages: Sequence[BaseMessage], limit: int = HISTORY_LIMIT) -> list[BaseMessage]:
    """The last messages of the history, at most about `limit`, starting at a user
    message so no tool message is separated from the AI message that called it. When
    the last user exchange alone is longer, it is kept whole."""
    from langchain_core.messages import HumanMessage

    if len(messages) <= limit:
        return list(messages)
    starts = [i for i, m in enumerate(messages) if isinstance(m, HumanMessage)]
    for start in starts:
        if len(messages) - start <= limit:
            return list(messages[start:])
    return list(messages[starts[-1] :]) if starts else []


def model_input(
    round_: RoundContext,
    history: Sequence[BaseMessage],
    context: str,
    turn: Sequence[BaseMessage],
) -> list[BaseMessage]:
    """System prompt, capped history, the context message, then this turn's messages
    (the user's message first, when there is one)."""
    from langchain_core.messages import HumanMessage, SystemMessage

    return [
        SystemMessage(round_.ctx.texts.prompt("intake", "agent")),
        *cut_history(history),
        HumanMessage(context),
        *turn,
    ]


def _invoke(model: Any, messages: list[BaseMessage]) -> Any:
    """`model.invoke`, retrying transient errors as structured output does."""
    for attempt in range(TRANSIENT_ATTEMPTS):
        try:
            return model.invoke(messages)
        except Exception as exc:
            if attempt + 1 >= TRANSIENT_ATTEMPTS or not is_transient(exc):
                raise
            structured.default_sleep(TRANSIENT_DELAYS[min(attempt, len(TRANSIENT_DELAYS) - 1)])
    raise AssertionError("unreachable")  # pragma: no cover


def _invalid(detail: str) -> str:
    if len(detail) > ERROR_LIMIT:
        detail = detail[: ERROR_LIMIT - 1] + "…"
    return json.dumps({"error": "invalid_arguments", "detail": detail}, ensure_ascii=False)


def _not_run(reason: str) -> str:
    return json.dumps({"error": "not_run", "detail": reason})


def run_turn(
    round_: RoundContext,
    model: Any,
    history: Sequence[BaseMessage],
    user_message: str | None = None,
) -> TurnOutcome:
    """One agent turn (see the module doc). `model` is the tool-bound chat model
    (`bind`); `history` the round's messages so far, `user_message` the user's new
    message (`None` for the opening turn). The reply, the "go on" line or the error
    line is printed here."""
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    ctx = round_.ctx
    if user_message is not None:
        round_.data.user_messages.append(user_message)
    context = round_.context_payload()
    turn: list[BaseMessage] = [HumanMessage(user_message)] if user_message is not None else []
    handlers = round_.handlers()
    texts: list[str] = []
    calls = 0
    retried = False

    def fail(error: str) -> TurnOutcome:
        return failed_turn(ctx, user_message, error, tool_calls=calls)

    def end(kind: TurnKind) -> TurnOutcome:
        text = "\n\n".join(texts)
        if kind == "capped" and not text:
            text = ctx.msg("intake.agent.go_on")
        if text:
            say(ctx.console, text)
        return TurnOutcome(kind, text=text, messages=turn, tool_calls=calls)

    while True:
        try:
            reply = _invoke(model, model_input(round_, history, context, turn))
        except Exception as exc:  # providers raise their own types
            return fail(short_error(exc))
        if not isinstance(reply, AIMessage):
            return fail("no reply")
        turn.append(reply)
        text = message_text(reply).strip()
        if text:
            texts.append(text)
        requests: list[tuple[str, str, Any, str | None]] = [
            (c.get("id") or "", c.get("name", ""), c.get("args") or {}, None)
            for c in reply.tool_calls
        ]
        requests += [
            (c.get("id") or "", c.get("name") or "", {}, c.get("error") or "unparsable arguments")
            for c in reply.invalid_tool_calls
        ]
        if not requests:
            return end("reply") if text else fail("empty reply")
        invalid = False
        stop: TurnKind | None = None
        for call_id, name, args, parse_error in requests:
            if stop is not None:
                why = "round finished" if stop == "finished" else "tool-call limit of the turn"
                turn.append(ToolMessage(_not_run(why), tool_call_id=call_id, name=name))
                continue
            calls += 1
            content: str
            if name not in handlers:
                invalid = True
                content = _invalid(f"unknown tool {name!r}; the tools are {', '.join(handlers)}")
            elif parse_error is not None:
                invalid = True
                content = _invalid(parse_error)
            else:
                schema, handler = handlers[name]
                try:
                    parsed = schema.model_validate(args)
                except ValidationError as exc:
                    invalid = True
                    content = _invalid(str(exc))
                else:
                    content = handler(parsed).model_dump_json()
            turn.append(ToolMessage(content, tool_call_id=call_id, name=name))
            if round_.data.finished:
                stop = "finished"
            elif calls >= MAX_TOOL_CALLS:
                stop = "capped"
        if stop is not None:
            return end(stop)
        if invalid:
            if retried:
                return fail("invalid tool arguments")
            retried = True


__all__ = [
    "HISTORY_LIMIT",
    "MAX_TOOL_CALLS",
    "TOOL_NAMES",
    "TurnOutcome",
    "bind",
    "build_tools",
    "cut_history",
    "failed_turn",
    "model_input",
    "run_turn",
]
