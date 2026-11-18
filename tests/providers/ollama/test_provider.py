import pytest
from helpers import ScriptedPrompter, fake_model, hardware, make_ctx, tool_call
from langchain_core.messages import AIMessage

from app.providers.base import SetupAborted
from app.providers.ollama.provider import OllamaProvider
from app.settings import Settings


def test_availability():
    p = OllamaProvider()
    assert p.availability(hardware(ram=16)).available
    a = p.availability(hardware(ram=4, vram=0))
    assert not a.available and a.reason_key == "ollama.no_fit"


def test_tier_menu_lists_fitting_with_best_marked(workspace):
    prompter = ScriptedPrompter("qwen3.5:9b-q4_K_M")
    ctx, _ = make_ctx(workspace, prompter)
    tag = OllamaProvider().choose_tier(ctx, hardware(ram=64, vram=8))
    call = prompter.choose_calls()[0]
    values = [c.value for c in call["choices"]]
    assert call["default"] == "qwen3.5:9b-q4_K_M" == tag
    assert values[:-1] == [
        "qwen3.6:35b-a3b-q8_0",
        "qwen3.6:35b-a3b-q4_K_M",
        "qwen3.5:9b-q4_K_M",
        "qwen3.5:4b-q4_K_M",
        "qwen3.5:2b-q4_K_M",
    ]
    best = next(c for c in call["choices"] if c.value == tag)
    assert ctx.msg("ollama.tier_best") in best.label


def test_run_setup_saves_url_and_model(workspace, monkeypatch):
    class Srv:
        def __init__(self, ctx):
            pass

        def ensure(self, hw, base):
            return "http://localhost:11436"

    pulled = []
    monkeypatch.setattr(
        "app.providers.ollama.provider.pull_model",
        lambda ctx, base, tag: pulled.append((base, tag)),
    )
    ctx, _ = make_ctx(workspace, ScriptedPrompter("qwen3.5:4b-q4_K_M"))
    result = OllamaProvider(server_factory=Srv).run_setup(ctx, hardware(ram=8))
    assert result.model == "qwen3.5:4b-q4_K_M"
    assert result.updates == {"ollama_base_url": "http://localhost:11436"}
    assert pulled == [("http://localhost:11436", "qwen3.5:4b-q4_K_M")]


def _verify_with(workspace, monkeypatch, model):
    monkeypatch.setattr(OllamaProvider, "chat_model", lambda self, s, c: model)
    ctx, _ = make_ctx(workspace)
    OllamaProvider().verify(ctx, Settings(provider="ollama", model="m"))


def test_verify_pass(workspace, monkeypatch):
    _verify_with(
        workspace, monkeypatch, fake_model("ready", tool_call("ping", {"value": 7}), "done")
    )


def test_verify_fails_without_tool_call(workspace, monkeypatch):
    with pytest.raises(SetupAborted) as exc:
        _verify_with(workspace, monkeypatch, fake_model("ready", "I will not call tools"))
    assert "tool" in exc.value.message or "nástroj" in exc.value.message


def test_verify_fails_on_empty_reply(workspace, monkeypatch):
    with pytest.raises(SetupAborted):
        _verify_with(workspace, monkeypatch, fake_model(AIMessage("")))


def test_verify_model_without_tools(workspace, monkeypatch):
    class NoTools(type(fake_model())):
        def bind_tools(self, *a, **k):
            raise NotImplementedError

    with pytest.raises(SetupAborted) as exc:
        _verify_with(
            workspace, monkeypatch, NoTools(messages=iter([AIMessage("ready")]), received=[])
        )
    assert exc.value.message == make_ctx(workspace)[0].msg(
        "verify.no_tools", provider=make_ctx(workspace)[0].msg("provider.ollama.label")
    )


def test_verify_connection_error(workspace, monkeypatch):
    class Down(type(fake_model())):
        def _generate(self, *a, **k):
            raise ConnectionError("connection refused")

    with pytest.raises(SetupAborted, match="connection refused"):
        _verify_with(workspace, monkeypatch, Down(messages=iter([]), received=[]))
