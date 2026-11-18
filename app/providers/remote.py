"""Shared steps for remote providers: the data notice and hidden key entry."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.providers.base import SetupAborted

if TYPE_CHECKING:
    from app.tools.context import ToolContext

KEYLESS_PLACEHOLDER = "not-needed"


def confirm_data_notice(ctx: ToolContext) -> bool:
    """Show that prompts and data leave the machine; return the user's answer."""
    ctx.console.print(f"[yellow]{ctx.msg('remote.notice')}[/yellow]", highlight=False)
    return ctx.confirm(ctx.msg("remote.confirm"))


def require_data_notice(ctx: ToolContext) -> None:
    if not confirm_data_notice(ctx):
        raise SetupAborted(ctx.msg("setup.declined"))


def ask_key(ctx: ToolContext, env_name: str, *, optional: bool = False) -> str | None:
    """Hidden input (never in the REPL history); stored in `<workspace>/.env` (0600).

    An empty answer keeps an existing key; for an optional key it means "none".
    """
    existing = ctx.credentials.get(env_name)
    key = (
        "remote.key_prompt_keep"
        if existing
        else ("remote.key_prompt_optional" if optional else "remote.key_prompt")
    )
    value = ctx.secret(ctx.msg(key, name=env_name))
    if value:
        ctx.credentials.set(env_name, value)
        ctx.say("remote.key_saved", path=str(ctx.credentials.env_file))
        return value
    if existing:
        return existing
    if optional:
        return None
    raise SetupAborted(ctx.msg("remote.key_missing"))
