import pytest
from helpers import ScriptedPrompter, fake_model, hardware, make_ctx, tool_call

from app.credentials import OPENAI_KEY, Credentials
from app.providers.base import SetupAborted
from app.providers.openai_compatible import OpenAICompatibleProvider, validate_base_url
from app.settings import Settings


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("http://localhost:1234/v1", True),
        ("https://api.example.com/v1", True),
        ("ftp://x/v1", False),
        ("localhost:1234", False),
        ("http://", False),
        ("", False),
    ],
)
def test_url_validation(url, ok):
    assert validate_base_url(url) is ok


def test_loopback_skips_notice_and_key_optional(workspace):
    prompter = ScriptedPrompter("http://127.0.0.1:1234/v1/", "", "local-model")
    ctx, _ = make_ctx(workspace, prompter)
    result = OpenAICompatibleProvider().run_setup(ctx, hardware())
    assert result.model == "local-model"
    assert result.updates == {"openai_base_url": "http://127.0.0.1:1234/v1"}
    assert [k for k, *_ in prompter.asked] == ["text", "secret", "text"]
    assert Credentials(workspace, {}).get(OPENAI_KEY) is None


def test_lan_host_gets_notice_and_key_held_until_verified(workspace):
    prompter = ScriptedPrompter("http://192.168.1.10:8000/v1", True, "lan-key-123456", "m")
    ctx, _ = make_ctx(workspace, prompter)
    OpenAICompatibleProvider().run_setup(ctx, hardware())
    assert [k for k, *_ in prompter.asked] == ["text", "confirm", "secret", "text"]
    assert ctx.credentials.get(OPENAI_KEY) == "lan-key-123456"  # used for verification
    assert Credentials(workspace, {}).get(OPENAI_KEY) is None  # not written yet


def test_lan_http_warns_cleartext_https_does_not(workspace):
    ctx, buf = make_ctx(workspace, ScriptedPrompter("http://192.168.1.10:8000/v1", False))
    with pytest.raises(SetupAborted):
        OpenAICompatibleProvider().run_setup(ctx, hardware())
    assert ctx.msg("remote.cleartext")[:20] in buf.getvalue()
    ctx, buf = make_ctx(workspace, ScriptedPrompter("https://api.example.com/v1", False))
    with pytest.raises(SetupAborted):
        OpenAICompatibleProvider().run_setup(ctx, hardware())
    assert ctx.msg("remote.cleartext")[:20] not in buf.getvalue()


def test_new_host_prompt_says_stored_key_goes_there(workspace):
    from app.settings import save_settings

    save_settings(
        workspace, Settings(provider="openai_compatible", openai_base_url="https://a.example/v1")
    )
    Credentials(workspace, {}).set(OPENAI_KEY, "old-key-123456")
    prompter = ScriptedPrompter("https://b.example/v1", True, "", "m")
    ctx, _ = make_ctx(workspace, prompter)
    OpenAICompatibleProvider().run_setup(ctx, hardware())
    secret_q = next(q for k, q, *_ in prompter.asked if k == "secret")
    assert "b.example" in secret_q
    assert secret_q == ctx.msg("remote.key_prompt_keep_host", name=OPENAI_KEY, host="b.example")


def test_same_host_prompt_keeps_plain_wording(workspace):
    from app.settings import save_settings

    save_settings(
        workspace, Settings(provider="openai_compatible", openai_base_url="https://a.example/v1")
    )
    Credentials(workspace, {}).set(OPENAI_KEY, "old-key-123456")
    prompter = ScriptedPrompter("https://a.example/v2", True, "", "m")
    ctx, _ = make_ctx(workspace, prompter)
    OpenAICompatibleProvider().run_setup(ctx, hardware())
    secret_q = next(q for k, q, *_ in prompter.asked if k == "secret")
    assert secret_q == ctx.msg("remote.key_prompt_keep", name=OPENAI_KEY)


def test_declined_notice(workspace):
    ctx, _ = make_ctx(workspace, ScriptedPrompter("https://api.example.com/v1", False))
    with pytest.raises(SetupAborted):
        OpenAICompatibleProvider().run_setup(ctx, hardware())


def test_verify_pass_and_fail(workspace, monkeypatch):
    ctx, _ = make_ctx(workspace)
    s = Settings(provider="openai_compatible", model="m", openai_base_url="http://localhost:1/v1")
    monkeypatch.setattr(
        OpenAICompatibleProvider,
        "chat_model",
        lambda self, s, c: fake_model("ok", tool_call("ping", {"value": 7}), "done"),
    )
    OpenAICompatibleProvider().verify(ctx, s)
    monkeypatch.setattr(
        OpenAICompatibleProvider, "chat_model", lambda self, s, c: fake_model("ok", "nope")
    )
    with pytest.raises(SetupAborted):
        OpenAICompatibleProvider().verify(ctx, s)


def test_endpoint_without_routes_fails_verification(workspace):
    import httpx2

    from app.providers.verify import verify_chat_model

    model = OpenAICompatibleProvider().chat_model(
        Settings(provider="openai_compatible", model="m", openai_base_url="http://localhost:1/v1"),
        Credentials(workspace, {}),
        http_client=httpx2.Client(
            transport=httpx2.MockTransport(lambda r: httpx2.Response(404, text="not found"))
        ),
    )
    ctx, _ = make_ctx(workspace)
    with pytest.raises(SetupAborted):
        verify_chat_model(ctx, model, "Custom")


def test_env_key_skips_prompt_and_says_so(workspace):
    prompter = ScriptedPrompter("https://api.example.com/v1", True, "m")
    ctx, buf = make_ctx(workspace, prompter, env={OPENAI_KEY: "env-key-123456"})
    OpenAICompatibleProvider().run_setup(ctx, hardware())
    assert [k for k, *_ in prompter.asked] == ["text", "confirm", "text"]
    assert ctx.msg("remote.key_from_env_host", name=OPENAI_KEY, host="api.example.com") in (
        " ".join(buf.getvalue().split())
    )
    assert "env-key-123456" not in buf.getvalue()
