from helpers import console, fake_model, make_ctx

from app.agent import default_model_factory
from app.tools import default_registry
from app.tools.context import ToolContext


def test_model_factory_defaults_to_the_chat_agents(workspace):
    con, _ = console()
    ctx = ToolContext(workspace=workspace, console=con, registry=default_registry())
    assert ctx.model_factory is default_model_factory


def test_make_ctx_default_and_injected_model_factory(workspace):
    ctx, _ = make_ctx(workspace)
    assert ctx.model_factory is default_model_factory
    model = fake_model("hi")
    ctx, _ = make_ctx(workspace, model_factory=lambda settings, credentials: model)
    assert ctx.model_factory(ctx.settings, ctx.credentials) is model


def test_repl_contexts_get_the_repls_model_factory(workspace):
    from app.repl import Repl

    model = fake_model("hi")

    def factory(settings, credentials):
        return model

    con, _ = console()
    repl = Repl(workspace, con, model_factory=factory)
    assert repl.make_ctx().model_factory is factory
