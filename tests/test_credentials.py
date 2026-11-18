import stat

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
