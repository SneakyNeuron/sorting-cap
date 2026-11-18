"""`-setup` (user-only, chat-only): language, hardware, then a provider selection loop.

The chosen provider runs its setup and verification; on failure or a declined
step the user returns to the menu with the reason and can pick again or quit.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import ValidationError

from app.errors import validation_message
from app.providers.base import Provider, SetupAborted, providers
from app.providers.remote import save_pending_keys
from app.schemas.setup import SetupInput, SetupOutput
from app.settings import Settings
from app.setup.hardware import Hardware, detect
from app.tools.context import Choice, ToolContext, language_choices
from app.tools.registry import Tool

QUIT = "quit"


def detect_hardware(ctx: ToolContext) -> Hardware:
    return detect(ctx.runner)


def menu_providers() -> list[Provider]:
    """Every registered provider, in menu order; unavailable ones are shown disabled."""
    return providers()


def _choose_language(ctx: ToolContext) -> None:
    language = ctx.choose(
        ctx.msg("setup.language_question"),
        language_choices(),
        default=ctx.settings.language,
    )
    ctx.save_settings(ctx.settings.model_copy(update={"language": language}))


def _hardware_summary(ctx: ToolContext, hw: Hardware) -> None:
    gpu = (
        ctx.msg("setup.hw_gpu", name=hw.gpu_name or "NVIDIA", vram=hw.vram_gb)
        if hw.vram_gb
        else ctx.msg("setup.hw_no_gpu")
    )
    if not hw.docker:
        docker = ctx.msg("setup.hw_docker_missing")
    elif not hw.docker_usable:
        docker = ctx.msg("setup.hw_docker_unusable")
    else:
        docker = ctx.msg("setup.hw_docker_gpu" if hw.docker_gpu else "setup.hw_docker")
    ctx.say("setup.hw_summary", style="bold", ram=hw.ram_gb, gpu=gpu, docker=docker)


def _menu(ctx: ToolContext, hw: Hardware) -> tuple[list[Choice], str | None]:
    choices: list[Choice] = []
    default: str | None = None
    for provider in menu_providers():
        availability = provider.availability(hw)
        label = ctx.msg(provider.label_key)
        if availability.available:
            choices.append(Choice(provider.name, label))
            if default is None and provider.preselectable:
                default = provider.name
        else:
            reason = ctx.msg(availability.reason_key or "setup.unavailable")
            ctx.console.print(reason, style="yellow", highlight=False, markup=False)
            choices.append(
                Choice(provider.name, label, disabled_reason=ctx.msg("setup.unavailable"))
            )
    choices.append(Choice(QUIT, ctx.msg("setup.menu_quit")))
    return choices, default


def _show_tour_once(ctx: ToolContext) -> None:
    if ctx.settings.tour_shown:
        return
    from rich.markdown import Markdown

    from app.tools.tour import tour_markdown

    ctx.console.print(Markdown(tour_markdown(ctx)))
    ctx.save_settings(ctx.settings.model_copy(update={"tour_shown": True}))


def handle(ctx: ToolContext, inputs: SetupInput) -> SetupOutput:
    try:
        return _run(ctx)
    finally:
        ctx.pending_keys.clear()  # unverified keys never outlive the setup run


def _run(ctx: ToolContext) -> SetupOutput:
    _choose_language(ctx)
    hw = detect_hardware(ctx)
    _hardware_summary(ctx, hw)
    if ctx.settings.provider and ctx.settings.setup_completed_at:
        ctx.say("setup.current", provider=ctx.settings.provider, model=ctx.settings.model or "—")
    while True:
        choices, default = _menu(ctx, hw)
        pick = ctx.choose(ctx.msg("setup.menu_question"), choices, default=default)
        if pick == QUIT:
            return SetupOutput(completed=False)
        provider = next(p for p in menu_providers() if p.name == pick)
        ctx.pending_keys.clear()
        try:
            result = provider.run_setup(ctx, hw)
            try:
                candidate = Settings.model_validate(
                    {
                        **ctx.settings.model_dump(),
                        **result.updates,
                        "provider": pick,
                        "model": result.model,
                    }
                )
            except ValidationError as exc:
                raise SetupAborted(
                    ctx.msg("setup.invalid_result", detail=validation_message(exc, ctx.texts))
                ) from exc
            ctx.say("setup.verifying", provider=ctx.msg(provider.label_key), model=result.model)
            provider.verify(ctx, candidate)
        except SetupAborted as exc:
            ctx.pending_keys.clear()
            ctx.console.print(exc.message, style="red", highlight=False, markup=False)
            ctx.say("setup.back_to_menu")
            continue
        candidate = candidate.model_copy(update={"setup_completed_at": datetime.now(UTC)})
        save_pending_keys(ctx)
        ctx.save_settings(candidate)
        ctx.say("setup.verified", style="green")
        _show_tour_once(ctx)
        return SetupOutput(completed=True, provider=candidate.provider, model=candidate.model)


TOOL = Tool(
    name="setup",
    input_model=SetupInput,
    output_model=SetupOutput,
    handler=handle,
    chat_only=True,
)
