"""Provider interface and registry.

A provider knows whether it can run here, sets itself up, verifies itself and
builds a LangChain chat model from settings. `-setup`'s menu and
`Settings.provider` validation both come from this registry.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.errors import UserFacingError

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

    from app.credentials import Credentials
    from app.settings import Settings
    from app.setup.hardware import Hardware
    from app.tools.context import ToolContext


class SetupAborted(UserFacingError):
    """A provider's setup or verification failed or a step was declined.

    `message` is already a user-facing text in the current language.
    """


@dataclass(frozen=True)
class Availability:
    available: bool
    reason_key: str | None = None
    detail: Any = None


@dataclass(frozen=True)
class SetupResult:
    """What a provider's setup decided; saved into settings after verification."""

    model: str
    updates: dict[str, Any] = field(default_factory=dict)


class Provider(ABC):
    name: str
    label_key: str

    @abstractmethod
    def is_remote(self, settings: Settings) -> bool: ...

    def endpoint_url(self, settings: Settings) -> str | None:
        """The user-configurable URL this provider talks to (None for a fixed service)."""
        return None

    @abstractmethod
    def availability(self, hardware: Hardware) -> Availability: ...

    @abstractmethod
    def run_setup(self, ctx: ToolContext, hardware: Hardware) -> SetupResult: ...

    @abstractmethod
    def verify(self, ctx: ToolContext, settings: Settings) -> None:
        """Raise `SetupAborted` with the reason when the provider does not work."""

    @abstractmethod
    def chat_model(self, settings: Settings, credentials: Credentials) -> BaseChatModel: ...


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
