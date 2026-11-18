import pytest
from helpers import FakeToolModel, fake_model, make_ctx, tool_call
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from app.providers import verify
from app.providers.base import SetupAborted
from app.providers.verify import verify_chat_model


class RaisingBindModel(FakeToolModel):
    """Answers the chat check, then fails when tools are bound."""

    error: str = ""

    def bind_tools(self, tools, **kwargs):
        raise RuntimeError(self.error)


class RecordingBindModel(FakeToolModel):
    bound: list = []

    def bind_tools(self, tools, **kwargs):
        self.bound.extend(tools)
        return self


def _ctx(workspace):
    ctx, _ = make_ctx(workspace)
    return ctx


def test_chat_and_tool_call_pass(workspace):
    verify_chat_model(
        _ctx(workspace), fake_model("ok", tool_call("ping", {"value": 7}), "done"), "P"
    )


@pytest.mark.parametrize("hint", verify._NO_TOOLS_HINTS)
def test_each_no_tools_hint_gives_no_tools_reason(workspace, hint):
    ctx = _ctx(workspace)
    model = RaisingBindModel(messages=iter([AIMessage("ok")]), received=[], error=f"400: {hint}")
    with pytest.raises(SetupAborted) as exc:
        verify_chat_model(ctx, model, "P")
    assert exc.value.message == ctx.msg("verify.no_tools", provider="P")


def test_hint_match_ignores_case(workspace):
    ctx = _ctx(workspace)
    model = RaisingBindModel(
        messages=iter([AIMessage("ok")]), received=[], error="Model DOES NOT SUPPORT TOOLS"
    )
    with pytest.raises(SetupAborted) as exc:
        verify_chat_model(ctx, model, "P")
    assert exc.value.message == ctx.msg("verify.no_tools", provider="P")


def test_not_implemented_bind_tools_gives_no_tools_reason(workspace):
    ctx = _ctx(workspace)
    model = GenericFakeChatModel(messages=iter([AIMessage("ok")]))  # no bind_tools
    with pytest.raises(SetupAborted) as exc:
        verify_chat_model(ctx, model, "P")
    assert exc.value.message == ctx.msg("verify.no_tools", provider="P")


def test_other_tool_step_error_is_generic_failure(workspace):
    ctx = _ctx(workspace)
    model = RaisingBindModel(
        messages=iter([AIMessage("ok")]), received=[], error="connection reset\nmore"
    )
    with pytest.raises(SetupAborted) as exc:
        verify_chat_model(ctx, model, "P")
    assert exc.value.message == ctx.msg("verify.failed", provider="P", detail="connection reset")


class FailingChatModel(FakeToolModel):
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise RuntimeError("401 invalid api key")


def test_chat_error_is_generic_failure(workspace):
    ctx = _ctx(workspace)
    with pytest.raises(SetupAborted) as exc:
        verify_chat_model(ctx, FailingChatModel(messages=iter([]), received=[]), "P")
    assert exc.value.message == ctx.msg("verify.failed", provider="P", detail="401 invalid api key")


def test_empty_reply(workspace):
    ctx = _ctx(workspace)
    with pytest.raises(SetupAborted) as exc:
        verify_chat_model(ctx, fake_model("  "), "P")
    assert exc.value.message == ctx.msg("verify.empty_reply", provider="P")


def test_no_tool_call(workspace):
    ctx = _ctx(workspace)
    with pytest.raises(SetupAborted) as exc:
        verify_chat_model(ctx, fake_model("ok", "I will not call it"), "P")
    assert exc.value.message == ctx.msg("verify.no_tool_call", provider="P")


@pytest.mark.parametrize("language", ["cs", "en"])
def test_probe_description_comes_from_prompt_file(workspace, language):
    ctx = _ctx(workspace)
    ctx.save_settings(ctx.settings.model_copy(update={"language": language}))
    model = RecordingBindModel(
        messages=iter([AIMessage("ok"), tool_call("ping", {"value": 7}), AIMessage("done")]),
        received=[],
        bound=[],
    )
    verify_chat_model(ctx, model, "P")
    assert {t.name for t in model.bound} == {"ping"}
    assert model.bound[0].description == ctx.texts.prompt("verify", "tool_description").strip()
