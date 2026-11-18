import io
import json

import pytest
from helpers import ScriptedPrompter, make_ctx

from app.credentials import NOVITA_KEY, Credentials
from app.schemas.settings import SettingsInput
from app.settings import Settings, load_settings, save_settings
from app.tools.context import ToolError
from app.tools.headless import run_headless
from app.tools.settings import handle


def test_show_masks_keys(workspace):
    Credentials(workspace, env={}).set(NOVITA_KEY, "sk-very-secret-key")
    ctx, buf = make_ctx(workspace)
    out = handle(ctx, SettingsInput())
    out.render(ctx)
    assert "sk-very-secret-key" not in buf.getvalue() + out.model_dump_json()
    assert out.settings["language"] == "cs"


def test_set_values(workspace):
    ctx, _ = make_ctx(workspace)
    out = handle(ctx, SettingsInput(language="en", ollama_base_url="http://localhost:9999/"))
    assert set(out.changed) == {"language", "ollama_base_url"}
    s = load_settings(workspace)
    assert (s.language, s.ollama_base_url) == ("en", "http://localhost:9999")


def test_provider_validated_against_registry(workspace):
    ctx, _ = make_ctx(workspace)
    with pytest.raises(ToolError, match="provider"):
        handle(ctx, SettingsInput(provider="openai"))
    assert not (workspace / "settings.json").exists()


def test_invalid_provider_message_in_users_language(workspace):
    ctx, _ = make_ctx(workspace)  # default settings: Czech
    with pytest.raises(ToolError) as exc:
        handle(ctx, SettingsInput(provider="foo"))
    assert exc.value.message.startswith("Neplatné nastavení: provider: neznámý poskytovatel 'foo'")
    assert "unknown" not in exc.value.message


def test_provider_change_without_model_clears_model(workspace):
    save_settings(workspace, Settings(provider="ollama", model="qwen3.5:9b-q4_K_M"))
    ctx, _ = make_ctx(workspace)
    out = handle(
        ctx, SettingsInput(provider="openai_compatible", openai_base_url="http://localhost:1/v1")
    )
    assert load_settings(workspace).model is None
    assert out.notes


def test_remote_notice_confirm(workspace):
    prompter = ScriptedPrompter(True)
    ctx, buf = make_ctx(workspace, prompter)
    handle(ctx, SettingsInput(provider="novita", model="m"))
    assert load_settings(workspace).provider == "novita"
    assert ctx.msg("remote.notice") in buf.getvalue().replace("\n", " ")


def test_remote_notice_decline_changes_nothing(workspace):
    save_settings(workspace, Settings(provider="ollama", model="x"))
    before = (workspace / "settings.json").read_text()
    ctx, _ = make_ctx(workspace, ScriptedPrompter(False))
    with pytest.raises(ToolError):
        handle(ctx, SettingsInput(provider="novita", model="m"))
    assert (workspace / "settings.json").read_text() == before


def test_lan_url_needs_notice_loopback_does_not(workspace):
    ctx, _ = make_ctx(workspace)  # NoPrompts: any prompt fails
    handle(
        ctx,
        SettingsInput(
            provider="openai_compatible", openai_base_url="http://127.0.0.1:1234/v1", model="m"
        ),
    )
    ctx, _ = make_ctx(workspace, ScriptedPrompter(False))
    with pytest.raises(ToolError):
        handle(ctx, SettingsInput(openai_base_url="http://192.168.1.5:1234/v1"))
    assert load_settings(workspace).openai_base_url == "http://127.0.0.1:1234/v1"


def test_headless_remote_without_yes_denied(workspace):
    err = io.StringIO()
    assert run_headless(["settings", "--provider", "novita"], stdout=io.StringIO(), stderr=err) == 1
    assert load_settings(workspace).provider is None
    out = io.StringIO()
    assert (
        run_headless(
            ["settings", "--provider", "novita", "--model", "m", "--yes", "--json"], stdout=out
        )
        == 0
    )
    assert json.loads(out.getvalue())["settings"]["provider"] == "novita"


def test_remote_ollama_url_needs_notice(workspace):
    save_settings(workspace, Settings(provider="ollama", model="x"))
    ctx, buf = make_ctx(workspace, ScriptedPrompter(False))
    with pytest.raises(ToolError):
        handle(ctx, SettingsInput(ollama_base_url="http://203.0.113.5:11434"))
    assert load_settings(workspace).ollama_base_url == "http://localhost:11434"
    assert ctx.msg("remote.cleartext") in buf.getvalue().replace("\n", " ")
    ctx, _ = make_ctx(workspace, ScriptedPrompter(True))
    handle(ctx, SettingsInput(ollama_base_url="http://203.0.113.5:11434"))
    assert load_settings(workspace).ollama_base_url == "http://203.0.113.5:11434"


def test_https_remote_url_has_no_cleartext_warning(workspace):
    ctx, buf = make_ctx(workspace, ScriptedPrompter(True))
    handle(
        ctx,
        SettingsInput(
            provider="openai_compatible", openai_base_url="https://api.example.com/v1", model="m"
        ),
    )
    assert ctx.msg("remote.cleartext") not in buf.getvalue().replace("\n", " ")


def test_url_without_scheme_rejected(workspace):
    ctx, _ = make_ctx(workspace)
    with pytest.raises(ToolError, match="ollama_base_url"):
        handle(ctx, SettingsInput(ollama_base_url="localhost:11434"))
    assert not (workspace / "settings.json").exists()
