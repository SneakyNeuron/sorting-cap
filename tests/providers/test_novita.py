import json
from pathlib import Path

import httpx
import pytest
from helpers import ScriptedPrompter, fake_model, hardware, make_ctx, tool_call

from app.providers.base import SetupAborted
from app.providers.novita import TYPE_ID, NovitaProvider, function_calling_models
from app.settings import Settings

FIXTURE = json.loads((Path(__file__).parent.parent / "fixtures" / "novita_models.json").read_text())


def models_http(seen):
    def handler(request):
        seen.append(request)
        assert str(request.url) == "https://api.novita.ai/openai/v1/models"
        return httpx.Response(200, json=FIXTURE)

    return handler


def test_filter_function_calling():
    assert function_calling_models(FIXTURE) == [
        "deepseek/deepseek-v3.2",
        "qwen/qwen3-235b-a22b-instruct-2507",
    ]


def test_setup_lists_models_and_saves_pick(workspace):
    seen = []
    prompter = ScriptedPrompter(True, "nv-key-abcdef", "qwen/qwen3-235b-a22b-instruct-2507")
    ctx, buf = make_ctx(workspace, prompter, handler=models_http(seen))
    result = NovitaProvider().run_setup(ctx, hardware())
    assert result.model == "qwen/qwen3-235b-a22b-instruct-2507"
    call = prompter.choose_calls()[0]
    assert call["default"] is None
    assert [c.value for c in call["choices"]][:2] == function_calling_models(FIXTURE)
    assert seen[0].headers["authorization"] == "Bearer nv-key-abcdef"
    assert "nv-key-abcdef" not in buf.getvalue()


def test_type_an_id(workspace):
    prompter = ScriptedPrompter(True, "k" * 12, TYPE_ID, "custom/model")
    ctx, _ = make_ctx(workspace, prompter, handler=models_http([]))
    assert NovitaProvider().run_setup(ctx, hardware()).model == "custom/model"


def test_list_unavailable_falls_back_to_typing(workspace):
    ctx, _ = make_ctx(
        workspace,
        ScriptedPrompter(True, "k" * 12, "typed/model"),
        handler=lambda r: httpx.Response(500),
    )
    assert NovitaProvider().run_setup(ctx, hardware()).model == "typed/model"


def test_declining_notice_asks_nothing_else(workspace):
    prompter = ScriptedPrompter(False)
    ctx, _ = make_ctx(workspace, prompter)
    with pytest.raises(SetupAborted):
        NovitaProvider().run_setup(ctx, hardware())
    assert not (workspace / ".env").exists()


def test_verify_pass_and_fail(workspace, monkeypatch):
    ctx, _ = make_ctx(workspace)
    s = Settings(provider="novita", model="m")
    monkeypatch.setattr(
        NovitaProvider,
        "chat_model",
        lambda self, s, c: fake_model("ok", tool_call("ping", {"value": 7}), "done"),
    )
    NovitaProvider().verify(ctx, s)
    monkeypatch.setattr(
        NovitaProvider, "chat_model", lambda self, s, c: fake_model("ok", "no tools")
    )
    with pytest.raises(SetupAborted):
        NovitaProvider().verify(ctx, s)


def test_bad_key_verification_fails_with_reason(workspace):
    import httpx2

    from app.credentials import Credentials

    def handler(request):
        return httpx2.Response(401, json={"error": {"message": "invalid api key", "type": "auth"}})

    model = NovitaProvider().chat_model(
        Settings(provider="novita", model="m"),
        Credentials(workspace, {"NOVITA_API_KEY": "bad"}),
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )
    ctx, _ = make_ctx(workspace)
    from app.providers.verify import verify_chat_model

    with pytest.raises(SetupAborted, match="401|invalid api key"):
        verify_chat_model(ctx, model, "Novita")
