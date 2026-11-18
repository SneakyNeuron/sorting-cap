import json

import pytest
from helpers import fake_model, make_ctx, tool_call
from langchain_core.messages import AIMessage, HumanMessage

from app.agent import ChatAgent, NoModelConfigured, reasoning_of
from app.credentials import Credentials
from app.providers.base import get_provider
from app.schemas.base import ToolInput, ToolOutput
from app.settings import Settings
from app.tools import default_registry
from app.tools.registry import Tool


def agent_for(workspace, *models, registry=None):
    registry = registry or default_registry()
    queue = list(models)
    agent = ChatAgent(
        registry, lambda: make_ctx(workspace, registry=registry)[0], lambda s, c: queue.pop(0)
    )
    return agent


SETTINGS = Settings(language="en", provider="ollama", model="m")


def test_text_reply(workspace):
    agent = agent_for(workspace, fake_model("hello"))
    reply = agent.ask("hi", SETTINGS, Credentials(workspace, {}))
    assert reply.answer == "hello" and reply.reasoning is None


def test_no_model_configured(workspace):
    with pytest.raises(NoModelConfigured):
        agent_for(workspace).ask("hi", Settings(), Credentials(workspace, {}))


def test_tool_call_to_help_runs_the_tool(workspace):
    model = fake_model(tool_call("help"), "Here are the commands.")
    agent = agent_for(workspace, model)
    reply = agent.ask("what can you do", SETTINGS, Credentials(workspace, {}))
    assert reply.answer == "Here are the commands."
    tool_messages = [m for m in agent.history if m.type == "tool"]
    assert tool_messages and tool_messages[0].name == "help"
    assert "settings" in json.loads(tool_messages[0].content)["commands"][2]["name"]


def test_tool_list_only_agent_callable(workspace, lenient_texts):
    class In(ToolInput):
        pass

    class Out(ToolOutput):
        pass

    registry = default_registry()
    registry.register(Tool("useronly", In, Out, lambda c, i: Out()))
    agent = agent_for(workspace, fake_model("x"), registry=registry)
    agent.ask("hi", SETTINGS, Credentials(workspace, {}))
    assert agent.tool_names == ["help", "tour"]
    assert "settings" not in agent.tool_names and "setup" not in agent.tool_names


def test_rebuilt_with_history_kept_after_change(workspace):
    second = fake_model("second")
    agent = agent_for(
        workspace, fake_model("first"), second, fake_model("third"), fake_model("fourth")
    )
    creds = Credentials(workspace, {})
    agent.ask("one", SETTINGS, creds)
    agent.ask("two", SETTINGS.model_copy(update={"model": "other"}), creds)
    assert agent.builds == 2
    sent = second.received[0]
    assert [m.content for m in sent if isinstance(m, HumanMessage)] == ["one", "two"]
    agent.ask("three", SETTINGS.model_copy(update={"model": "other", "language": "cs"}), creds)
    agent.ask(
        "four",
        SETTINGS.model_copy(update={"provider": "novita", "model": "other", "language": "cs"}),
        creds,
    )
    assert agent.builds == 4
    assert len([m for m in agent.history if isinstance(m, HumanMessage)]) == 4


def test_system_prompt_follows_language(workspace):
    model_en, model_cs = fake_model("a"), fake_model("b")
    agent = agent_for(workspace, model_en, model_cs)
    creds = Credentials(workspace, {})
    agent.ask("x", SETTINGS, creds)
    agent.ask("y", SETTINGS.model_copy(update={"language": "cs"}), creds)
    assert "You are Sorting Cap" in model_en.received[0][0].content
    assert "Jsi Sorting Cap" in model_cs.received[0][0].content


def test_reasoning_from_fake_model(workspace):
    msg = AIMessage("ans", additional_kwargs={"reasoning_content": " deep thought "})
    reply = agent_for(workspace, fake_model(msg)).ask("q", SETTINGS, Credentials(workspace, {}))
    assert (reply.answer, reply.reasoning) == ("ans", "deep thought")
    assert reasoning_of(AIMessage("x")) is None


def test_chat_ollama_built_with_reasoning(workspace):
    from langchain_ollama import ChatOllama

    model = get_provider("ollama").chat_model(
        Settings(
            provider="ollama", model="qwen3.5:9b-q4_K_M", ollama_base_url="http://localhost:9"
        ),
        Credentials(workspace, {}),
    )
    assert isinstance(model, ChatOllama)
    assert model.reasoning is True
    assert model.base_url == "http://localhost:9"


def _openai_http(captured):
    import httpx2

    def handler(request):
        captured.append(request)
        body = {
            "id": "x",
            "object": "chat.completion",
            "created": 1,
            "model": "m",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": "final answer",
                        "reasoning_content": "hidden steps",
                    },
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        return httpx2.Response(200, json=body)

    return httpx2.Client(transport=httpx2.MockTransport(handler))


@pytest.mark.parametrize("provider", ["novita", "openai_compatible"])
def test_reasoning_passes_through_chatdeepseek(workspace, provider):
    from langchain_deepseek import ChatDeepSeek

    captured = []
    settings = Settings(
        provider=provider, model="some/model", openai_base_url="http://127.0.0.1:5/v1"
    )
    model = get_provider(provider).chat_model(
        settings, Credentials(workspace, {}), http_client=_openai_http(captured)
    )
    assert isinstance(model, ChatDeepSeek)
    agent = ChatAgent(default_registry(), lambda: make_ctx(workspace)[0], lambda s, c: model)
    reply = agent.ask("q", settings, Credentials(workspace, {}))
    assert (reply.answer, reply.reasoning) == ("final answer", "hidden steps")
    expected = (
        "https://api.novita.ai/openai/v1" if provider == "novita" else "http://127.0.0.1:5/v1"
    )
    assert str(captured[0].url) == f"{expected}/chat/completions"


def test_deepseek_env_key_never_sent_to_custom_endpoint(workspace, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-secret-from-env")
    monkeypatch.setenv("DEEPSEEK_API_BASE", "https://evil.example/v1")
    captured = []
    settings = Settings(
        provider="openai_compatible", model="m", openai_base_url="http://127.0.0.1:5/v1"
    )
    model = get_provider("openai_compatible").chat_model(
        settings, Credentials(workspace, {}), http_client=_openai_http(captured)
    )
    model.invoke("hi")
    request = captured[0]
    assert str(request.url).startswith("http://127.0.0.1:5/v1")
    assert request.headers["authorization"] == "Bearer not-needed"
    assert "ds-secret-from-env" not in str(request.headers)


def test_novita_key_sent(workspace):
    captured = []
    creds = Credentials(workspace, {"NOVITA_API_KEY": "nv-key"})
    model = get_provider("novita").chat_model(
        Settings(provider="novita", model="m"), creds, http_client=_openai_http(captured)
    )
    model.invoke("hi")
    assert captured[0].headers["authorization"] == "Bearer nv-key"


def test_chat_model_per_provider(workspace):
    from langchain_deepseek import ChatDeepSeek
    from langchain_ollama import ChatOllama

    creds = Credentials(workspace, {})
    assert isinstance(
        get_provider("ollama").chat_model(Settings(provider="ollama", model="m"), creds), ChatOllama
    )
    for name in ("novita", "openai_compatible"):
        s = Settings(provider=name, model="m", openai_base_url="http://localhost:1/v1")
        assert isinstance(get_provider(name).chat_model(s, creds), ChatDeepSeek)


def test_model_error_propagates_and_history_untouched(workspace):
    class Exploding(type(fake_model())):
        def _generate(self, *a, **k):
            raise ConnectionError("down")

    agent = agent_for(workspace, Exploding(messages=iter([]), received=[]))
    with pytest.raises(ConnectionError):
        agent.ask("hi", SETTINGS, Credentials(workspace, {}))
    assert agent.history == []


def test_broken_settings_during_tool_call_reported_to_agent(workspace):
    (workspace / "settings.json").write_text("{oops")
    model = fake_model(tool_call("help"), "The settings file is broken.")
    agent = agent_for(workspace, model)
    reply = agent.ask("what can you do", SETTINGS, Credentials(workspace, {}))
    assert reply.answer == "The settings file is broken."
    tool_messages = [m for m in agent.history if m.type == "tool"]
    assert "is not valid JSON (line 1, column 2)" in tool_messages[0].content
