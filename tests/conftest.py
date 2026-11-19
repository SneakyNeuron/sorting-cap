import pytest

SENSITIVE = (
    "NOVITA_API_KEY",
    "SORTINGCAP_OPENAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_API_BASE",
    "OPENAI_API_KEY",
    "LANGSMITH_GATEWAY",
)


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Every test gets a temporary home and workspace and no real keys."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SORTINGCAP_WORKSPACE", str(tmp_path / "ws"))
    for name in SENSITIVE:
        monkeypatch.delenv(name, raising=False)
    return home


@pytest.fixture
def workspace(tmp_path):
    path = tmp_path / "ws"
    path.mkdir(exist_ok=True)
    return path


@pytest.fixture
def lenient_texts(monkeypatch):
    """Stub tools in tests have no catalog entries: fall back to the key itself."""
    from app.texts import Texts

    original = Texts.message

    def message(self, key, **values):
        return original(self, key, **values) if self.has(key) else key

    monkeypatch.setattr("app.tools.registry.has_message", lambda lang, key: True)
    monkeypatch.setattr(Texts, "message", message)


@pytest.fixture(autouse=True)
def no_retry_waits(monkeypatch):
    """Transient-error retries of structured-output calls never sleep in tests."""
    monkeypatch.setattr("app.providers.structured.default_sleep", lambda _seconds: None)
