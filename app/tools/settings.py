"""`/settings` (user-only): show settings, or change them.

A plain edit: no key prompts and no verification (that is `/setup`'s job).
"""

from __future__ import annotations

from pydantic import ValidationError

from app.credentials import KNOWN_KEYS, mask
from app.errors import validation_message
from app.providers.remote import confirm_data_notice
from app.schemas.settings import SettingsInput, SettingsOutput
from app.settings import Settings
from app.tools.context import ToolContext, ToolError
from app.tools.registry import Tool


def _output(ctx: ToolContext, settings: Settings, changed=(), notes=()) -> SettingsOutput:
    creds = ctx.credentials
    return SettingsOutput(
        settings=settings.model_dump(mode="json"),
        keys={k: mask(creds.get(k)) for k in KNOWN_KEYS},
        changed=list(changed),
        notes=list(notes),
    )


def handle(ctx: ToolContext, inputs: SettingsInput) -> SettingsOutput:
    current = ctx.settings
    updates = inputs.model_dump(exclude_none=True)
    if not updates:
        return _output(ctx, current)
    notes = []
    if "provider" in updates and updates["provider"] != current.provider and "model" not in updates:
        updates["model"] = None
        notes.append(ctx.msg("settings.model_cleared"))
    try:
        candidate = Settings.model_validate({**current.model_dump(), **updates})
    except ValidationError as exc:
        detail = validation_message(exc, ctx.texts)
        raise ToolError(ctx.msg("settings.invalid", detail=detail)) from exc
    becomes_remote = candidate.is_remote() and (
        not current.is_remote()
        or candidate.provider != current.provider
        or candidate.endpoint_url() != current.endpoint_url()
    )
    if becomes_remote and not confirm_data_notice(ctx, candidate.endpoint_url()):
        raise ToolError(ctx.msg("settings.remote_declined"))
    changed = [k for k, v in candidate.model_dump().items() if v != current.model_dump()[k]]
    ctx.save_settings(candidate)
    return _output(ctx, candidate, changed, notes)


TOOL = Tool(name="settings", input_model=SettingsInput, output_model=SettingsOutput, handler=handle)
