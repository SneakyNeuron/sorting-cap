"""Shared steps for remote providers: the data notice and hidden key entry."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.credentials import check_value
from app.providers.base import SetupAborted
from app.providers.hosts import is_cleartext_remote

if TYPE_CHECKING:
    from app.tools.context import ToolContext

KEYLESS_PLACEHOLDER = "not-needed"


def confirm_data_notice(ctx: ToolContext, url: str | None = None) -> bool:
    """Show that prompts and data leave the machine; return the user's answer.

    A plain `http://` URL to another host also gets a cleartext warning.
    """
    ctx.console.print(ctx.msg("remote.notice"), style="yellow", highlight=False, markup=False)
    if is_cleartext_remote(url):
        ctx.console.print(
            ctx.msg("remote.cleartext"), style="yellow", highlight=False, markup=False
        )
    return ctx.confirm(ctx.msg("remote.confirm"))


def require_data_notice(ctx: ToolContext, url: str | None = None) -> None:
    if not confirm_data_notice(ctx, url):
        raise SetupAborted(ctx.msg("setup.declined"))


def ask_key(
    ctx: ToolContext, env_name: str, *, optional: bool = False, new_host: str | None = None
) -> str | None:
    """Hidden input (never in the REPL history), kept in `ctx.pending_keys`.

    The key is not written yet: `/setup` saves it to `<workspace>/.env` (0600)
    only after verification succeeds, so a wrong key never replaces a working one.
    When the variable is set in the environment it wins anyway: no prompt, the
    user is told so. An empty answer keeps an existing key; for an optional key
    it means "none". `new_host` is set when the endpoint's host changed: the
    prompt then says that keeping the stored key sends it to that host.
    """
    credentials = ctx.credentials
    from_env = credentials.from_env(env_name)
    if from_env:
        if new_host:
            ctx.say("remote.key_from_env_host", style="yellow", name=env_name, host=new_host)
        else:
            ctx.say("remote.key_from_env", style="yellow", name=env_name)
        return from_env
    existing = credentials.get(env_name)
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
            check_value(env_name, value)
        except ValueError as exc:
            raise SetupAborted(ctx.msg("remote.key_invalid")) from exc
        ctx.pending_keys[env_name] = value
        return value
    if existing:
        return existing
    if optional:
        return None
    raise SetupAborted(ctx.msg("remote.key_missing"))


def save_pending_keys(ctx: ToolContext) -> None:
    """Write the keys typed during this `/setup` round to `.env`, then forget them."""
    for name, value in list(ctx.pending_keys.items()):
        ctx.credentials.set(name, value)
        ctx.say("remote.key_saved", path=str(ctx.credentials.env_file))
    ctx.pending_keys.clear()
