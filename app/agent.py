"""The chat agent: LangChain `create_agent` over the agent-callable tools only.

The REPL owns the conversation as a plain message list, passed in full on every
turn, so rebuilding the agent after a settings change keeps the history.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.credentials import Credentials
from app.errors import UserFacingError
from app.messages import message_text
from app.providers.base import ModelFactory, default_model_factory, get_provider
from app.settings import Settings
from app.texts import Texts
from app.tools.context import ToolContext
from app.tools.registry import Registry

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool

__all__ = ["ChatAgent", "ModelFactory", "NoModelConfigured", "Reply", "default_model_factory"]


class NoModelConfigured(Exception):
    """No provider and model yet: the user must run `-setup` first."""


@dataclass(frozen=True)
class Reply:
    answer: str
    reasoning: str | None = None


def wrap_tools(
    registry: Registry, ctx_factory: Callable[[], ToolContext], texts: Texts
) -> list[BaseTool]:
    """LangChain tools for the `agent_callable` registry tools, and nothing else."""
    from langchain_core.tools import StructuredTool

    wrapped = []
    for tool in registry.agent_tools():

        def _run(_tool=tool, **kwargs: Any) -> str:
            try:
                ctx = ctx_factory()  # reads settings.json, which may be broken by now
                inputs = _tool.input_model.model_validate(kwargs)
                return _tool.run(ctx, inputs).as_agent_text()
            except UserFacingError as exc:
                return exc.text(texts)

        wrapped.append(
            StructuredTool.from_function(
                func=_run,
                name=tool.name,
                description=texts.message(tool.description_key),
                args_schema=tool.input_model,
            )
        )
    return wrapped


def reasoning_of(message: Any) -> str | None:
    reasoning = (getattr(message, "additional_kwargs", None) or {}).get("reasoning_content")
    if not reasoning and isinstance(getattr(message, "content", None), list):
        parts = [
            b.get("reasoning", "")
            for b in message.content
            if isinstance(b, dict) and b.get("type") == "reasoning"
        ]
        reasoning = "".join(parts)
    return reasoning.strip() if isinstance(reasoning, str) and reasoning.strip() else None


class ChatAgent:
    def __init__(
        self,
        registry: Registry,
        ctx_factory: Callable[[], ToolContext],
        model_factory: ModelFactory = default_model_factory,
    ) -> None:
        self.registry = registry
        self.ctx_factory = ctx_factory
        self.model_factory = model_factory
        self.history: list[Any] = []
        self._agent = None
        self._key: tuple | None = None
        # Introspection only (how often the agent was rebuilt, which tools it got);
        # nothing in the app reads them, tests do.
        self.builds = 0
        self.tool_names: list[str] = []

    @staticmethod
    def _settings_key(settings: Settings, credentials: Credentials) -> tuple:
        """What the built agent depends on; the provider's key only as a SHA-256 digest."""
        key_name = get_provider(settings.provider).key_name if settings.provider else None
        key = credentials.get(key_name) if key_name else None
        return (
            settings.provider,
            settings.model,
            settings.language,
            settings.ollama_base_url,
            settings.openai_base_url,
            hashlib.sha256(key.encode()).hexdigest() if key else None,
        )

    def _build(self, settings: Settings, credentials: Credentials) -> None:
        from langchain.agents import create_agent

        texts = Texts(settings.language)
        tools = wrap_tools(self.registry, self.ctx_factory, texts)
        self.tool_names = [t.name for t in tools]
        self._agent = create_agent(
            self.model_factory(settings, credentials),
            tools=tools,
            system_prompt=texts.prompt("chat", "system"),
        )
        self._key = self._settings_key(settings, credentials)
        self.builds += 1

    def ask(self, text: str, settings: Settings, credentials: Credentials) -> Reply:
        from langchain_core.messages import AIMessage, HumanMessage

        if not settings.provider or not settings.model:
            raise NoModelConfigured
        if self._agent is None or self._settings_key(settings, credentials) != self._key:
            self._build(settings, credentials)
        result = self._agent.invoke({"messages": [*self.history, HumanMessage(text)]})
        messages = result["messages"]
        self.history = list(messages)
        last = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
        if last is None:
            return Reply(answer="")
        return Reply(answer=message_text(last), reasoning=reasoning_of(last))
