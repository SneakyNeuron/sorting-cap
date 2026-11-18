"""Provider interface and registry.

A provider knows whether it can run here, sets itself up, verifies itself and
builds a LangChain chat model from settings. `-setup`'s menu and
`Settings.provider` validation both come from this registry.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.errors import UserFacingError

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

    from app.credentials import Credentials
    from app.settings import Settings
    from app.setup.hardware import Hardware
    from app.tools.context import Choice, ToolContext


class SetupAborted(UserFacingError):
    """A provider's setup or verification failed or a step was declined.

    `message` is already a user-facing text in the current language.
    """


@dataclass(frozen=True)
class Availability:
    available: bool
    reason_key: str | None = None


@dataclass(frozen=True)
class SetupResult:
    """What a provider's setup decided; saved into settings after verification."""

    model: str
    updates: dict[str, Any] = field(default_factory=dict)


class Provider(ABC):
    name: str
    label_key: str
    key_name: str | None = None  # the credential (env var / `.env` name) this provider uses
    # R9: preselected in the `-setup` menu when available; only local providers set it
    preselectable: bool = False

    @abstractmethod
    def is_remote(self, settings: Settings) -> bool: ...

    def endpoint_url(self, settings: Settings) -> str | None:
        """The user-configurable URL this provider talks to (None for a fixed service)."""
        return None

    @abstractmethod
    def availability(self, hardware: Hardware) -> Availability: ...

    @abstractmethod
    def run_setup(self, ctx: ToolContext, hardware: Hardware) -> SetupResult: ...

    def verify(self, ctx: ToolContext, settings: Settings) -> None:
        """Raise `SetupAborted` with the reason when the provider does not work.

        The default builds the chat model with `ctx.credentials` (which include
        keys typed in this `-setup` run, not yet in `.env`) and runs the shared
        chat-reply plus tool-call check.
        """
        from app.providers.verify import verify_chat_model

        verify_chat_model(ctx, self.chat_model(settings, ctx.credentials), ctx.msg(self.label_key))

    @abstractmethod
    def chat_model(
        self, settings: Settings, credentials: Credentials, http_client: Any = None
    ) -> BaseChatModel:
        """The LangChain chat model for `settings`.

        `http_client` replaces the model's HTTP client where its LangChain class
        accepts one (an `httpx2.Client` for the OpenAI-compatible providers, used by
        tests to fake HTTP); providers whose model takes none ignore it.
        """


def choose_or_back(
    ctx: ToolContext, question: str, choices: list[Choice], default: str | None = None
) -> str:
    """`ctx.choose` with a final "back to the menu" choice; picking it aborts the step."""
    from app.tools.context import BACK
    from app.tools.context import Choice as _Choice

    pick = ctx.choose(question, [*choices, _Choice(BACK, ctx.msg("setup.back"))], default=default)
    if pick == BACK:
        raise SetupAborted(ctx.msg("setup.declined"))
    return pick


_REGISTRY: dict[str, Provider] = {}


def register_provider(provider: Provider) -> Provider:
    if provider.name in _REGISTRY:
        raise ValueError(f"provider {provider.name!r} is already registered")
    _REGISTRY[provider.name] = provider
    return provider


def _load_builtin() -> None:
    if not _REGISTRY:
        from app.providers import builtin  # noqa: F401  (registers on import)


def provider_names() -> list[str]:
    _load_builtin()
    return list(_REGISTRY)


def providers() -> list[Provider]:
    _load_builtin()
    return list(_REGISTRY.values())


def get_provider(name: str) -> Provider:
    _load_builtin()
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        raise KeyError(f"unknown provider: {name!r}") from exc


ModelFactory = Callable[["Settings", "Credentials"], "BaseChatModel"]


def default_model_factory(settings: Settings, credentials: Credentials) -> BaseChatModel:
    """The configured provider's chat model: what the chat agent and tools use by default."""
    return get_provider(settings.provider).chat_model(settings, credentials)
