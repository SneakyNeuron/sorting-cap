"""The `ollama` provider: local, default, preselected when the hardware fits."""

from __future__ import annotations

from collections.abc import Callable

from app.providers.base import Availability, Provider, SetupResult, choose_or_back
from app.providers.hosts import is_loopback_url
from app.providers.ollama.pull import pull_model
from app.providers.ollama.server import OllamaServer
from app.providers.ollama.tiers import fitting_tiers, preselected_tier
from app.providers.remote import require_data_notice
from app.tools.context import Choice


class OllamaProvider(Provider):
    name = "ollama"
    label_key = "provider.ollama.label"
    preselectable = True

    def __init__(self, server_factory: Callable[..., OllamaServer] = OllamaServer) -> None:
        self.server_factory = server_factory

    def is_remote(self, settings) -> bool:
        """Local by default; an `ollama_base_url` on another host is remote (R10)."""
        return not is_loopback_url(settings.ollama_base_url)

    def endpoint_url(self, settings) -> str | None:
        return settings.ollama_base_url

    def availability(self, hardware) -> Availability:
        if not fitting_tiers(hardware):
            return Availability(False, reason_key="ollama.no_fit")
        return Availability(True)

    def choose_tier(self, ctx, hardware) -> str:
        best = preselected_tier(hardware)
        choices = [
            Choice(
                t.tag,
                ctx.msg(
                    "ollama.tier_label",
                    number=t.number,
                    tag=t.tag,
                    description=t.description,
                    size=t.download_gb,
                )
                + (f" — {ctx.msg('ollama.tier_best')}" if best and t.tag == best.tag else ""),
            )
            for t in fitting_tiers(hardware)
        ]
        return choose_or_back(
            ctx, ctx.msg("ollama.tier_question"), choices, default=best.tag if best else None
        )

    def run_setup(self, ctx, hardware) -> SetupResult:
        if not is_loopback_url(ctx.settings.ollama_base_url):
            require_data_notice(ctx, ctx.settings.ollama_base_url)
        base_url = self.server_factory(ctx).ensure(hardware, ctx.settings.ollama_base_url)
        tag = self.choose_tier(ctx, hardware)
        pull_model(ctx, base_url, tag)
        return SetupResult(model=tag, updates={"ollama_base_url": base_url})

    def chat_model(self, settings, credentials, http_client=None):
        """`ChatOllama` takes no HTTP client object, so `http_client` is ignored."""
        from langchain_ollama import ChatOllama

        return ChatOllama(model=settings.model, base_url=settings.ollama_base_url, reasoning=True)
