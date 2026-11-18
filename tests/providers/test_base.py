import pytest

from app.providers.base import get_provider, provider_names
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
