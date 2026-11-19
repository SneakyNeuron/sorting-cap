"""Registers the Phase 1 providers. Order is the order of `/setup`'s menu."""

from app.providers.base import register_provider
from app.providers.novita import NovitaProvider
from app.providers.ollama.provider import OllamaProvider
from app.providers.openai_compatible import OpenAICompatibleProvider

register_provider(OllamaProvider())
register_provider(NovitaProvider())
register_provider(OpenAICompatibleProvider())
