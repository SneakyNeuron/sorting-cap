import json

import pytest
from helpers import FakeRunner, ScriptedPrompter, hardware, make_ctx
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from app.credentials import NOVITA_KEY
from app.providers.base import Availability, Provider, SetupAborted, SetupResult
from app.schemas.setup import SetupInput
from app.settings import Settings, load_settings, save_settings
from app.setup import flow


class FakeProvider(Provider):
    def __init__(
        self, name, *, available=True, remote=False, preselectable=False, fail=None, model="m1"
    ):
        self.name = name
        self.label_key = "provider.ollama.label" if name == "local" else "provider.novita.label"
        self._available = available
        self._remote = remote
        self.preselectable = preselectable
        self.fail = list(fail or [])
        self.model = model
        self.setups = 0

    def is_remote(self, settings):
        return self._remote

    def availability(self, hw):
        return Availability(True) if self._available else Availability(False, "ollama.no_fit")

    def run_setup(self, ctx, hw):
        self.setups += 1
        if self.fail and self.fail.pop(0) == "setup":
            raise SetupAborted("setup broke")
        return SetupResult(model=self.model)

    def verify(self, ctx, settings):
        if self.fail and self.fail.pop(0) == "verify":
            raise SetupAborted("verify broke")

    def chat_model(self, settings, credentials):
        raise NotImplementedError


@pytest.fixture
def fakes(monkeypatch):
    local = FakeProvider("local", preselectable=True)
    remote = FakeProvider("remote", remote=True, model="r1")
    monkeypatch.setattr(flow, "available_providers", lambda: [local, remote])
    monkeypatch.setattr(flow, "detect_hardware", lambda ctx: hardware(ram=16))
    monkeypatch.setattr(
        "app.settings.provider_names",
        lambda: ["local", "remote", "ollama", "novita", "openai_compatible"],
    )
    return local, remote


def run(workspace, *answers):
    prompter = ScriptedPrompter(*answers)
    ctx, buf = make_ctx(workspace, prompter)
    out = flow.handle(ctx, SetupInput())
    return out, prompter, buf.getvalue()


def test_language_saved_immediately(workspace, fakes):
    out, *_ = run(workspace, "en", "quit")
    assert load_settings(workspace).language == "en"
    assert not out.completed


def test_menu_preselects_local_never_remote(workspace, fakes):
    _, prompter, _ = run(workspace, "cs", "quit")
    menu = prompter.choose_calls()[1]
    assert menu["default"] == "local"
    assert [c.value for c in menu["choices"]] == ["local", "remote", "quit"]


def test_nothing_preselected_when_local_unavailable_with_message(workspace, fakes):
    local, _ = fakes
    local._available = False
    _, prompter, out = run(workspace, "en", "quit")
    menu = prompter.choose_calls()[1]
    assert menu["default"] is None
    assert next(c for c in menu["choices"] if c.value == "local").disabled_reason
    assert "not sufficient" in out and "Novita" in out


def test_failure_returns_to_menu_then_success(workspace, fakes):
    local, remote = fakes
    local.fail = ["setup"]
    out, prompter, text = run(workspace, "en", "local", "remote")
    assert "setup broke" in text and "Back to the menu" in text
    assert out.completed and out.provider == "remote" and out.model == "r1"
    assert len(prompter.choose_calls()) == 3


def test_verify_failure_returns_to_menu(workspace, fakes):
    local, _ = fakes
    local.fail = [None, "verify"]
    local.fail = ["ok", "verify"]
    out, _, text = run(workspace, "en", "local", "quit")
    assert "verify broke" in text and not out.completed
    assert load_settings(workspace).setup_completed_at is None


def test_quit_leaves_setup_incomplete(workspace, fakes):
    out, *_ = run(workspace, "en", "quit")
    s = load_settings(workspace)
    assert (out.completed, s.provider, s.setup_completed_at) == (False, None, None)


def test_success_saves_settings_and_shows_tour_once(workspace, fakes):
    out, _, text = run(workspace, "en", "local")
    s = load_settings(workspace)
    assert (s.provider, s.model, s.tour_shown) == ("local", "m1", True)
    assert s.setup_completed_at is not None
    assert "Next steps" in text
    out, prompter, text = run(workspace, "en", "local")
    assert "Next steps" not in text
    assert "Currently using local, model m1" in text


def test_hardware_summary_shown(workspace, fakes):
    _, _, text = run(workspace, "en", "quit")
    assert "RAM: 16 GB" in text


def test_real_providers_menu(workspace, monkeypatch):
    monkeypatch.setattr(flow, "detect_hardware", lambda ctx: hardware(ram=4, vram=0))
    _, prompter, text = run(workspace, "en", "quit")
    menu = prompter.choose_calls()[1]
    assert [c.value for c in menu["choices"]] == ["ollama", "novita", "openai_compatible", "quit"]
    assert menu["default"] is None
    monkeypatch.setattr(flow, "detect_hardware", lambda ctx: hardware(ram=16, vram=0))
    _, prompter, _ = run(workspace, "en", "quit")
    assert prompter.choose_calls()[1]["default"] == "ollama"


def test_setup_refused_headless(workspace):
    from app.tools.headless import run_headless

    assert run_headless(["setup"]) == 2
    assert run_headless(["setup", "--yes"]) == 2


def test_novita_key_never_in_settings_logs_or_history(workspace, monkeypatch):
    """End to end through the REPL: key typed at the hidden prompt."""
    import httpx
    from helpers import console, fake_model, tool_call

    from app.providers.novita import NovitaProvider
    from app.repl import Repl

    monkeypatch.setattr(flow, "detect_hardware", lambda ctx: hardware(ram=4))
    monkeypatch.setattr(
        NovitaProvider,
        "chat_model",
        lambda self, s, c: fake_model("ok", tool_call("ping", {"value": 7}), "done"),
    )
    fixture = json.loads(
        (
            __import__("pathlib").Path(__file__).parent.parent / "fixtures" / "novita_models.json"
        ).read_text()
    )
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=fixture))
    save_settings(workspace, Settings(language="en"))
    con, buf = console()
    secret = "nv-super-secret-0042"
    keys = "-setup\r\r1\ry\r" + secret + "\r1\r-settings\r-exit\r"
    with create_pipe_input() as inp, create_app_session(input=inp, output=DummyOutput()):
        repl = Repl(
            workspace,
            con,
            ctx_kwargs={"http_transport": transport, "runner_factory": lambda c: FakeRunner()},
        )
        inp.send_text(keys)
        inp.close()
        repl.run()
    s = load_settings(workspace)
    assert (s.provider, s.model) == ("novita", "deepseek/deepseek-v3.2")
    assert secret not in (workspace / "settings.json").read_text()
    assert secret not in (workspace / ".history").read_text()
    log = workspace / "logs" / "setup.log"
    assert not log.exists() or secret not in log.read_text()
    assert secret not in buf.getvalue()
    from app.credentials import Credentials

    assert Credentials(workspace, {}).get(NOVITA_KEY) == secret


def test_invalid_setup_result_shows_short_validation_message(workspace, fakes):
    local, _ = fakes
    local.run_setup = lambda ctx, hw: SetupResult(model="m", updates={"ollama_base_url": "x[/y]"})
    out, _, text = run(workspace, "en", "local", "quit")
    assert out.completed is False
    assert "Cannot save the setup: ollama_base_url: expected an http:// or https:// URL" in text
    assert "x[/y]" in text and "pydantic" not in text and "input_value" not in text
