"""The `novita` provider (remote, opt-in, always available, no default model)."""

from __future__ import annotations

import httpx

from app.credentials import NOVITA_KEY
from app.providers.base import Availability, Provider, SetupAborted, SetupResult
from app.providers.openai_api import openai_compatible_model
from app.providers.remote import ask_key, require_data_notice
from app.providers.verify import verify_chat_model
from app.tools.context import Choice

NOVITA_BASE_URL = "https://api.novita.ai/openai/v1"
TYPE_ID = "__type__"


def function_calling_models(payload: dict) -> list[str]:
    """Model ids whose `features` list includes `function-calling`."""
    return sorted(
        str(m["id"])
        for m in payload.get("data", [])
        if isinstance(m, dict) and m.get("id") and "function-calling" in (m.get("features") or [])
    )


class NovitaProvider(Provider):
    name = "novita"
    key_name = NOVITA_KEY
    label_key = "provider.novita.label"
    base_url = NOVITA_BASE_URL

    def is_remote(self, settings) -> bool:
        return True

    def availability(self, hardware) -> Availability:
        return Availability(True)

    def fetch_models(self, ctx, key: str) -> list[str] | None:
        try:
            with ctx.http_client(timeout=15) as client:
                response = client.get(
                    f"{self.base_url}/models", headers={"Authorization": f"Bearer {key}"}
                )
                response.raise_for_status()
                return function_calling_models(response.json())
        except (httpx.HTTPError, ValueError):
            return None

    def run_setup(self, ctx, hardware) -> SetupResult:
        require_data_notice(ctx)
        key = ask_key(ctx, NOVITA_KEY)
        models = self.fetch_models(ctx, key)
        if models:
            choices = [Choice(m, m) for m in models]
            choices += [
                Choice(TYPE_ID, ctx.msg("novita.type_id")),
                Choice("back", ctx.msg("setup.back")),
            ]
            pick = ctx.choose(ctx.msg("novita.model_question"), choices, default=None)
            if pick == "back":
                raise SetupAborted(ctx.msg("setup.declined"))
        else:
            ctx.say("novita.list_failed", style="yellow")
            pick = TYPE_ID
        if pick == TYPE_ID:
            pick = ctx.text(
                ctx.msg("novita.model_prompt"),
                validate=lambda v: None if v else ctx.msg("novita.model_required"),
            )
        return SetupResult(model=pick)

    def verify(self, ctx, settings) -> None:
        verify_chat_model(ctx, self.chat_model(settings, ctx.credentials), ctx.msg(self.label_key))

    def chat_model(self, settings, credentials, http_client=None):
        return openai_compatible_model(
            model=settings.model,
            api_base=self.base_url,
            api_key=credentials.get(NOVITA_KEY),
            http_client=http_client,
        )
