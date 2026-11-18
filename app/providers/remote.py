"""Shared steps for remote providers: the data notice and hidden key entry."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.providers.base import SetupAborted
from app.providers.hosts import is_cleartext_remote

if TYPE_CHECKING:
    from app.tools.context import ToolContext

KEYLESS_PLACEHOLDER = "not-needed"


def confirm_data_notice(ctx: ToolContext, url: str | None = None) -> bool:
    """Show that prompts and data leave the machine; return the user's answer.

    A plain `http://` URL to another host also gets a cleartext warning.
    """
    ctx.console.print(f"[yellow]{ctx.msg('remote.notice')}[/yellow]", highlight=False)
    if is_cleartext_remote(url):
        ctx.console.print(f"[yellow]{ctx.msg('remote.cleartext')}[/yellow]", highlight=False)
    return ctx.confirm(ctx.msg("remote.confirm"))


def require_data_notice(ctx: ToolContext, url: str | None = None) -> None:
    if not confirm_data_notice(ctx, url):
        raise SetupAborted(ctx.msg("setup.declined"))


def ask_key(
    ctx: ToolContext, env_name: str, *, optional: bool = False, new_host: str | None = None
) -> str | None:
    """Hidden input (never in the REPL history); stored in `<workspace>/.env` (0600).

    An empty answer keeps an existing key; for an optional key it means "none".
    `new_host` is set when the endpoint's host changed: the prompt then says that
    keeping the stored key sends it to that host.
    """
    existing = ctx.credentials.get(env_name)
    if existing and new_host:
        question = ctx.msg("remote.key_prompt_keep_host", name=env_name, host=new_host)
    elif existing:
        question = ctx.msg("remote.key_prompt_keep", name=env_name)
    elif optional:
        question = ctx.msg("remote.key_prompt_optional", name=env_name)
    else:
        question = ctx.msg("remote.key_prompt", name=env_name)
    value = ctx.secret(question)
    if value:
        try:
            ctx.credentials.set(env_name, value)
        except ValueError as exc:
            raise SetupAborted(ctx.msg("remote.key_invalid")) from exc
        ctx.say("remote.key_saved", path=str(ctx.credentials.env_file))
        return value
    if existing:
        return existing
    if optional:
        return None
    raise SetupAborted(ctx.msg("remote.key_missing"))
