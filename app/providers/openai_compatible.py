"""The `openai_compatible` provider: any OpenAI-compatible endpoint.

The base URL includes its version path (e.g. `http://localhost:1234/v1`); the
client appends `/chat/completions`. A loopback host counts as local.
"""

from __future__ import annotations

from app.credentials import OPENAI_KEY
from app.providers.base import Availability, Provider, SetupResult
from app.providers.hosts import host_of, is_http_url, is_loopback_url
from app.providers.openai_api import openai_compatible_model
from app.providers.remote import ask_key, require_data_notice


def validate_base_url(url: str) -> bool:
    return is_http_url(url)


class OpenAICompatibleProvider(Provider):
    name = "openai_compatible"
    key_name = OPENAI_KEY
    label_key = "provider.openai_compatible.label"

    def is_remote(self, settings) -> bool:
        return not is_loopback_url(settings.openai_base_url)

    def endpoint_url(self, settings) -> str | None:
        return settings.openai_base_url

    def availability(self, hardware) -> Availability:
        return Availability(True)

    def run_setup(self, ctx, hardware) -> SetupResult:
        url = (
            ctx.text(
                ctx.msg("openai.url_prompt"),
                default=ctx.settings.openai_base_url,
                validate=lambda v: None if validate_base_url(v) else ctx.msg("openai.url_invalid"),
            )
            .strip()
            .rstrip("/")
        )
        if not is_loopback_url(url):
            require_data_notice(ctx, url)
        previous = ctx.settings.openai_base_url
        new_host = host_of(url) if host_of(url) != host_of(previous) else None
        ask_key(ctx, OPENAI_KEY, optional=True, new_host=new_host)
        model = ctx.text(
            ctx.msg("openai.model_prompt"),
            default=ctx.settings.model if ctx.settings.provider == self.name else None,
            validate=lambda v: None if v else ctx.msg("openai.model_required"),
        )
        return SetupResult(model=model, updates={"openai_base_url": url})

    def chat_model(self, settings, credentials, http_client=None):
        return openai_compatible_model(
            model=settings.model,
            api_base=settings.openai_base_url or "",
            api_key=credentials.get(OPENAI_KEY),
            http_client=http_client,
        )
