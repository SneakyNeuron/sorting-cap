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


def test_lan_host_gets_notice_and_key_saved(workspace):
    prompter = ScriptedPrompter("http://192.168.1.10:8000/v1", True, "lan-key-123456", "m")
    ctx, _ = make_ctx(workspace, prompter)
    OpenAICompatibleProvider().run_setup(ctx, hardware())
    assert [k for k, *_ in prompter.asked] == ["text", "confirm", "secret", "text"]
    assert Credentials(workspace, {}).get(OPENAI_KEY) == "lan-key-123456"


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
