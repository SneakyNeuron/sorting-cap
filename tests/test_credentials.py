import os
import stat

import pytest
from helpers import make_ctx

from app.credentials import NOVITA_KEY, OPENAI_KEY, Credentials, mask


def test_env_file_written_0600_and_updated(workspace):
    creds = Credentials(workspace, env={})
    creds.set(NOVITA_KEY, "sk-first-key-123")
    path = workspace / ".env"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    creds.set(OPENAI_KEY, "other")
    creds.set(NOVITA_KEY, "sk-second-key-456")
    assert creds.get(NOVITA_KEY) == "sk-second-key-456"
    assert creds.get(OPENAI_KEY) == "other"
    assert path.read_text().count(NOVITA_KEY) == 1
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_env_var_precedence(workspace):
    Credentials(workspace, env={}).set(NOVITA_KEY, "from-file")
    assert Credentials(workspace, env={NOVITA_KEY: "from-env"}).get(NOVITA_KEY) == "from-env"
    assert Credentials(workspace, env={}).get(NOVITA_KEY) == "from-file"


def test_parser_handles_comments_quotes_export(workspace):
    (workspace / ".env").write_text('# c\nexport NOVITA_API_KEY="quoted"\n\nJUNK\n')
    assert Credentials(workspace, env={}).get(NOVITA_KEY) == "quoted"


def test_missing_key_is_none(workspace):
    assert Credentials(workspace, env={}).get(NOVITA_KEY) is None


def test_mask():
    assert mask(None) == "—"
    assert "secret-value-123" not in mask("secret-value-123")


@pytest.mark.parametrize("value", ["a\nOTHER=x", "a\rb", "a\x00b", "a\tb", ""])
def test_set_rejects_control_characters_and_empty(workspace, value):
    creds = Credentials(workspace, env={})
    creds.set(OPENAI_KEY, "keep-me")
    with pytest.raises(ValueError):
        creds.set(NOVITA_KEY, value)
    assert (workspace / ".env").read_text() == f"{OPENAI_KEY}=keep-me\n"


def test_loose_existing_env_never_holds_key_with_old_mode(workspace, monkeypatch):
    path = workspace / ".env"
    path.write_text("# mine\n")
    path.chmod(0o644)
    seen = []
    real_replace = os.replace

    def spy(src, dst):
        seen.append(stat.S_IMODE(os.stat(src).st_mode))
        real_replace(src, dst)

    monkeypatch.setattr("app.credentials.os.replace", spy)
    Credentials(workspace, env={}).set(NOVITA_KEY, "sk-secret-key-1")
    assert seen == [0o600]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.read_text() == f"# mine\n{NOVITA_KEY}=sk-secret-key-1\n"
    assert [p.name for p in workspace.iterdir()] == [".env"]


def test_repr_never_shows_keys(workspace):
    creds = Credentials(workspace, env={NOVITA_KEY: "sk-env-secret-1"}, overrides={OPENAI_KEY: "x"})
    assert "sk-env-secret-1" not in repr(creds)
    ctx, _ = make_ctx(workspace, env={NOVITA_KEY: "sk-env-secret-1"})
    ctx.pending_keys[OPENAI_KEY] = "sk-typed-secret-2"
    assert "sk-env-secret-1" not in repr(ctx)
    assert "sk-typed-secret-2" not in repr(ctx)
    assert "sk-env-secret-1" not in repr(ctx.credentials)
