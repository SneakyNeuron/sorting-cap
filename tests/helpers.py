"""Shared test doubles: fake chat model, scripted prompts, fake command runner."""

from __future__ import annotations

import io
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from rich.console import Console

from app.setup.hardware import Hardware
from app.setup.runner import CommandResult
from app.tools.context import Choice, InputUnavailable, ToolContext


class FakeToolModel(GenericFakeChatModel):
    """GenericFakeChatModel that accepts `bind_tools` and records what it was sent."""

    received: list = []

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.received.append(list(messages))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


@dataclass
class RawReply:
    """A queued `include_raw=True` reply: the raw message, the parsed value, the error."""

    raw: Any = None
    parsed: Any = None
    parsing_error: BaseException | None = None


class FakeStructuredModel(GenericFakeChatModel):
    """A chat model whose `with_structured_output(schema)` returns queued replies.

    Each queued reply is a Pydantic object or a dict (validated into the schema) that
    the next structured call returns, an exception it raises, or a `RawReply`. With
    `include_raw=True` the call returns `{"raw", "parsed", "parsing_error"}` like the
    LangChain models do: a dict that fails validation and an `OutputParserException`
    become `parsing_error` instead of being raised. `calls` records the messages of
    every structured call, `options` the `method` / `include_raw` of every
    `with_structured_output`; an empty queue fails the test.
    """

    queue: list = []
    calls: list = []
    schemas: list = []
    options: list = []

    def with_structured_output(self, schema, *, include_raw=False, **kwargs):
        from langchain_core.exceptions import OutputParserException
        from langchain_core.runnables import RunnableLambda
        from pydantic import ValidationError

        self.schemas.append(schema)
        self.options.append({"method": kwargs.get("method"), "include_raw": include_raw})

        def _plain(reply):
            if isinstance(reply, RawReply):
                if reply.parsing_error is not None:
                    raise reply.parsing_error
                return reply.parsed
            if isinstance(reply, BaseException):
                raise reply
            if isinstance(reply, dict):
                return schema.model_validate(reply)
            return reply

        def _raw(reply):
            if isinstance(reply, RawReply):
                return {
                    "raw": reply.raw,
                    "parsed": reply.parsed,
                    "parsing_error": reply.parsing_error,
                }
            if isinstance(reply, OutputParserException):
                return {"raw": AIMessage(str(reply)), "parsed": None, "parsing_error": reply}
            if isinstance(reply, BaseException):
                raise reply
            if isinstance(reply, dict):
                raw = AIMessage(json.dumps(reply))
                try:
                    parsed = schema.model_validate(reply)
                except ValidationError as exc:
                    error = OutputParserException(str(exc), llm_output=raw.content)
                    return {"raw": raw, "parsed": None, "parsing_error": error}
                return {"raw": raw, "parsed": parsed, "parsing_error": None}
            raw = AIMessage(reply.model_dump_json() if hasattr(reply, "model_dump_json") else "")
            return {"raw": raw, "parsed": reply, "parsing_error": None}

        def _answer(messages):
            self.calls.append(list(messages) if isinstance(messages, list) else messages)
            if not self.queue:
                raise AssertionError("unexpected structured-output call")
            reply = self.queue.pop(0)
            return _raw(reply) if include_raw else _plain(reply)

        return RunnableLambda(_answer)


def structured_model(*replies: Any) -> FakeStructuredModel:
    return FakeStructuredModel(
        messages=iter([]), queue=list(replies), calls=[], schemas=[], options=[]
    )


def fake_model(*replies: AIMessage | str) -> FakeToolModel:
    msgs = [AIMessage(r) if isinstance(r, str) else r for r in replies]
    return FakeToolModel(messages=iter(msgs), received=[])


def tool_call(name: str, args: dict | None = None, call_id: str = "call-1") -> AIMessage:
    return AIMessage("", tool_calls=[{"name": name, "args": args or {}, "id": call_id}])


class ScriptedPrompter:
    """Answers prompts from a script; records every question asked."""

    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.asked: list[tuple[str, str, Any]] = []

    def _next(self, kind: str, question: str, extra: Any = None):
        self.asked.append((kind, question, extra))
        if not self.answers:
            raise AssertionError(f"unexpected {kind} prompt: {question}")
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def confirm(self, question: str, default: bool = False) -> bool:
        answer = self._next("confirm", question)
        assert isinstance(answer, bool), f"confirm got {answer!r} for {question}"
        return answer

    def choose(self, question: str, choices: list[Choice], default: str | None = None) -> str:
        answer = self._next("choose", question, {"choices": choices, "default": default})
        enabled = [c.value for c in choices if not c.disabled_reason]
        assert answer in enabled, f"{answer!r} not selectable in {enabled}"
        return answer

    def text(self, question, default=None, validate=None) -> str:
        answer = self._next("text", question, default)
        if validate:
            problem = validate(answer)
            assert problem is None, problem
        return answer

    def secret(self, question: str) -> str:
        return self._next("secret", question)

    def choose_calls(self) -> list[dict]:
        return [extra for kind, _, extra in self.asked if kind == "choose"]


class NoPrompts(ScriptedPrompter):
    def _next(self, kind, question, extra=None):
        raise InputUnavailable(f"no prompts allowed: {question}")


@dataclass
class FakeRunner:
    """Records commands; `responder(args, mode)` returns a CommandResult."""

    responder: Callable[[list[str], str], CommandResult] = lambda args, mode: CommandResult(0)
    calls: list[tuple[list[str], str]] = field(default_factory=list)
    envs: list[dict | None] = field(default_factory=list)

    def run(self, args, *, mode="capture", timeout=None, log_name=None, env=None) -> CommandResult:
        self.calls.append((list(args), mode))
        self.envs.append(dict(env) if env else None)
        return self.responder(list(args), mode)

    def shell(self, script, *, mode="interactive", timeout=None) -> CommandResult:
        return self.run(["sh", "-c", script], mode=mode)

    def commands(self) -> list[str]:
        return [" ".join(a) for a, _ in self.calls]


def console() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(
        file=buf, width=120, force_terminal=False, color_system=None, highlight=False
    ), buf


def make_ctx(
    workspace: Path,
    prompter=None,
    *,
    registry=None,
    runner: FakeRunner | None = None,
    handler: Callable[[httpx.Request], httpx.Response] | None = None,
    env: dict | None = None,
    model_factory=None,
) -> tuple[ToolContext, io.StringIO]:
    from app.agent import default_model_factory
    from app.tools import default_registry

    con, buf = console()
    runner = runner or FakeRunner()
    ctx = ToolContext(
        workspace=workspace,
        console=con,
        registry=registry or default_registry(),
        prompter=prompter or NoPrompts(),
        env=env if env is not None else {},
        runner_factory=lambda _ctx: runner,
        http_transport=httpx.MockTransport(handler) if handler else httpx.MockTransport(_refuse),
        model_factory=model_factory or default_model_factory,
        resolve_host=_no_dns,
    )
    return ctx, buf


def _no_dns(host: str) -> list[str]:
    """No real lookups in tests: every name is unresolved (treated as public)."""
    return []


def _refuse(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


def hardware(
    ram: int = 16, vram: int = 0, docker: bool = True, usable: bool = True, gpu: bool = False
) -> Hardware:
    return Hardware(ram_gb=ram, vram_gb=vram, docker=docker, docker_usable=usable, docker_gpu=gpu)
