import pytest
from helpers import ScriptedPrompter, fake_model, make_ctx, tool_call

from app.credentials import NOVITA_KEY
from app.providers.base import (
    Availability,
    Provider,
    SetupAborted,
    SetupResult,
    choose_or_back,
    get_provider,
    provider_names,
)
from app.providers.hosts import is_loopback_url
from app.settings import Settings


def test_registered_providers():
    assert provider_names() == ["ollama", "novita", "openai_compatible"]
    with pytest.raises(KeyError):
        get_provider("nope")


@pytest.mark.parametrize(
    ("url", "loopback"),
    [
        ("http://localhost:1234/v1", True),
        ("http://LOCALHOST/v1", True),
        ("http://127.0.0.1:8000/v1", True),
        ("http://127.5.6.7/v1", True),
        ("http://[::1]:8080/v1", True),
        ("http://192.168.1.20:1234/v1", False),
        ("http://10.0.0.5/v1", False),
        ("https://api.example.com/v1", False),
        ("http://localhost.example.com/v1", False),
        (None, False),
        ("", False),
    ],
)
def test_loopback_helper(url, loopback):
    assert is_loopback_url(url) is loopback


def test_is_remote_per_provider():
    assert not Settings(provider="ollama").is_remote()
    assert Settings(provider="novita").is_remote()
    assert not Settings(
        provider="openai_compatible", openai_base_url="http://127.0.0.1:1234/v1"
    ).is_remote()
    assert Settings(
        provider="openai_compatible", openai_base_url="http://192.168.0.2:1234/v1"
    ).is_remote()
    assert not Settings().is_remote()


def test_ollama_remote_unless_loopback():
    assert not Settings(provider="ollama", ollama_base_url="http://[::1]:11434").is_remote()
    assert Settings(provider="ollama", ollama_base_url="http://203.0.113.5:11434").is_remote()


def test_only_local_ollama_is_preselectable():
    assert Provider.preselectable is False
    assert {n: get_provider(n).preselectable for n in provider_names()} == {
        "ollama": True,
        "novita": False,
        "openai_compatible": False,
    }


class MinimalProvider(Provider):
    """Implements only the abstract methods: `verify` is the shared default."""

    name = "minimal"
    label_key = "provider.novita.label"
    key_name = NOVITA_KEY

    def __init__(self):
        self.seen_keys = []

    def is_remote(self, settings):
        return True

    def availability(self, hardware):
        return Availability(True)

    def run_setup(self, ctx, hardware):
        return SetupResult(model="m")

    def chat_model(self, settings, credentials, http_client=None):
        self.seen_keys.append(credentials.get(NOVITA_KEY))
        return fake_model("ok", tool_call("ping", {"value": 7}), "done")


def test_default_verify_uses_pending_keys(workspace):
    ctx, _ = make_ctx(workspace)
    ctx.pending_keys[NOVITA_KEY] = "typed-not-saved-1"
    provider = MinimalProvider()
    provider.verify(ctx, Settings(provider="novita", model="m"))
    assert provider.seen_keys == ["typed-not-saved-1"]
    assert not (workspace / ".env").exists()


def test_choose_or_back(workspace):
    from app.tools.context import BACK, Choice

    prompter = ScriptedPrompter("a", BACK)
    ctx, _ = make_ctx(workspace, prompter)
    assert choose_or_back(ctx, "q", [Choice("a", "A")], default="a") == "a"
    call = prompter.choose_calls()[0]
    assert [c.value for c in call["choices"]] == ["a", BACK]
    assert call["default"] == "a"
    with pytest.raises(SetupAborted) as exc:
        choose_or_back(ctx, "q", [Choice("a", "A")])
    assert exc.value.message == ctx.msg("setup.declined")
