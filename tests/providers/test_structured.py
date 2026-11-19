"""The shared structured-output call: method per provider, parse retries, transient retries."""

from __future__ import annotations

import httpx
import pytest
from helpers import RawReply, structured_model
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel, Field

from app.providers import structured as so
from app.texts import Texts

EN = Texts("en")


class Answer(BaseModel):
    name: str = Field(description="A name.")
    count: int = Field(description="A count.")


MESSAGES = [SystemMessage("system"), HumanMessage("input")]


def call(model, sleep=None, **kwargs):
    return so.invoke_structured(
        model, Answer, list(MESSAGES), EN, sleep=sleep or (lambda _s: None), **kwargs
    )


def test_success_first_time():
    model = structured_model(Answer(name="a", count=1))
    assert call(model) == Answer(name="a", count=1)
    assert len(model.calls) == 1
    assert model.calls[0] == MESSAGES
    assert model.options == [{"method": None, "include_raw": True}]
    assert model.schemas == [Answer]


def test_dict_reply_is_validated():
    assert call(structured_model({"name": "b", "count": 2})) == Answer(name="b", count=2)


def test_invalid_then_valid_feeds_the_error_back():
    model = structured_model({"name": "a", "count": "many"}, Answer(name="a", count=3))
    assert call(model) == Answer(name="a", count=3)
    assert len(model.calls) == 2
    retry = model.calls[1]
    assert retry[:2] == MESSAGES
    raw, feedback = retry[2], retry[3]
    assert isinstance(raw, AIMessage) and "many" in raw.content
    assert isinstance(feedback, HumanMessage)
    assert "count" in feedback.content  # the validation error names the field
    assert feedback.content.startswith(EN.prompt("structured", "retry").split("{error}")[0])


def test_feedback_for_a_tool_call_reply_is_a_tool_message():
    raw = AIMessage("", tool_calls=[{"name": "Answer", "args": {"name": "a"}, "id": "call-7"}])
    model = structured_model(
        RawReply(raw=raw, parsing_error=ValueError("count: Field required")),
        Answer(name="a", count=1),
    )
    assert call(model) == Answer(name="a", count=1)
    retry = model.calls[1]
    assert retry[2] is raw
    assert isinstance(retry[3], ToolMessage) and retry[3].tool_call_id == "call-7"
    assert "Field required" in retry[3].content
    assert len(retry) == 4


def test_no_output_counts_as_invalid():
    model = structured_model(RawReply(raw=AIMessage("hello")), Answer(name="a", count=1))
    assert call(model) == Answer(name="a", count=1)
    assert len(model.calls) == 2


def test_three_invalid_replies_raise():
    bad = {"name": "a", "count": "many"}
    model = structured_model(bad, bad, bad)
    with pytest.raises(so.StructuredOutputError) as exc:
        call(model)
    assert len(model.calls) == 3
    assert "count" in str(exc.value)
    # each retry carries the history of earlier attempts
    assert len(model.calls[2]) == len(MESSAGES) + 4


@pytest.mark.parametrize(
    "error",
    [
        ConnectionError("refused"),
        TimeoutError("slow"),
        httpx.ConnectError("refused"),
        httpx.ReadTimeout("slow"),
    ],
)
def test_transient_error_then_success(error):
    waits: list[float] = []
    model = structured_model(error, Answer(name="a", count=1))
    assert call(model, sleep=waits.append) == Answer(name="a", count=1)
    assert len(model.calls) == 2
    assert model.calls[1] == MESSAGES  # nothing fed back for a transient error
    assert len(waits) == 1


def test_rate_limit_is_transient():
    class RateLimited(Exception):
        status_code = 429

    model = structured_model(RateLimited("slow down"), Answer(name="a", count=1))
    assert call(model) == Answer(name="a", count=1)


def test_transient_retries_are_bounded():
    waits: list[float] = []
    model = structured_model(*[ConnectionError("down")] * 3)
    with pytest.raises(ConnectionError):
        call(model, sleep=waits.append)
    assert len(model.calls) == so.TRANSIENT_ATTEMPTS == 3
    assert waits == list(so.TRANSIENT_DELAYS)


def test_other_errors_are_not_retried():
    model = structured_model(RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        call(model)
    assert len(model.calls) == 1


def test_method_is_passed_through(monkeypatch):
    monkeypatch.setattr(so, "structured_method", lambda _model: "json_schema")
    model = structured_model(Answer(name="a", count=1))
    call(model)
    assert model.options == [{"method": "json_schema", "include_raw": True}]


def test_method_per_provider():
    from langchain_deepseek import ChatDeepSeek
    from langchain_ollama import ChatOllama
    from pydantic import SecretStr

    ollama = ChatOllama(model="qwen3:8b", base_url="http://127.0.0.1:1")
    deepseek = ChatDeepSeek(model="m", api_base="http://127.0.0.1:1/v1", api_key=SecretStr("k"))
    assert so.structured_method(ollama) == "json_schema"
    assert so.structured_method(deepseek) == "function_calling"
    assert so.structured_method(structured_model()) is None
    for model in (ollama, deepseek):  # the real classes accept what the helper passes
        model.with_structured_output(Answer, method=so.structured_method(model), include_raw=True)


def test_default_sleep_is_patched_in_tests():
    """The suite never waits for real: `conftest` replaces the default sleep."""
    model = structured_model(ConnectionError("down"), Answer(name="a", count=1))
    assert so.invoke_structured(model, Answer, list(MESSAGES), EN) == Answer(name="a", count=1)
