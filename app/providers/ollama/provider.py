"""The `ollama` provider: local, default, preselected when the hardware fits."""

from __future__ import annotations

from collections.abc import Callable

from app.providers.base import Availability, Provider, SetupAborted, SetupResult
from app.providers.ollama.pull import pull_model
from app.providers.ollama.server import OllamaServer
from app.providers.ollama.tiers import fitting_tiers, preselected_tier
from app.providers.verify import verify_chat_model
from app.tools.context import Choice


class OllamaProvider(Provider):
    name = "ollama"
    label_key = "provider.ollama.label"
    preselectable = True

    def __init__(self, server_factory: Callable[..., OllamaServer] = OllamaServer) -> None:
        self.server_factory = server_factory

    def is_remote(self, settings) -> bool:
        return False

    def availability(self, hardware) -> Availability:
        tiers = fitting_tiers(hardware)
        if not tiers:
            return Availability(False, reason_key="ollama.no_fit")
        return Availability(True, detail=tiers)

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
        choices.append(Choice("back", ctx.msg("setup.back")))
        pick = ctx.choose(
            ctx.msg("ollama.tier_question"), choices, default=best.tag if best else None
        )
        if pick == "back":
            raise SetupAborted(ctx.msg("setup.declined"))
        return pick

    def run_setup(self, ctx, hardware) -> SetupResult:
        base_url = self.server_factory(ctx).ensure(hardware, ctx.settings.ollama_base_url)
        tag = self.choose_tier(ctx, hardware)
        pull_model(ctx, base_url, tag)
        return SetupResult(model=tag, updates={"ollama_base_url": base_url})

    def verify(self, ctx, settings) -> None:
        verify_chat_model(ctx, self.chat_model(settings, ctx.credentials), ctx.msg(self.label_key))

    def chat_model(self, settings, credentials):
        from langchain_ollama import ChatOllama

        return ChatOllama(model=settings.model, base_url=settings.ollama_base_url, reasoning=True)
