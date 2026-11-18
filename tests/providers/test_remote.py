import stat

import pytest
from helpers import ScriptedPrompter, make_ctx

from app.credentials import NOVITA_KEY, Credentials
from app.providers.base import SetupAborted
from app.providers.remote import ask_key, require_data_notice, save_pending_keys


def test_notice_decline_aborts(workspace):
    ctx, buf = make_ctx(workspace, ScriptedPrompter(False))
    with pytest.raises(SetupAborted):
        require_data_notice(ctx)
    assert ctx.msg("remote.notice")[:20] in buf.getvalue()


def test_key_held_until_saved_then_stored_0600_and_masked(workspace):
    ctx, buf = make_ctx(workspace, ScriptedPrompter("nv-secret-key-1234"))
    assert ask_key(ctx, NOVITA_KEY) == "nv-secret-key-1234"
    env = workspace / ".env"
    assert not env.exists()  # nothing written before verification
    assert ctx.credentials.get(NOVITA_KEY) == "nv-secret-key-1234"
    save_pending_keys(ctx)
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    assert Credentials(workspace, {}).get(NOVITA_KEY) == "nv-secret-key-1234"
    assert ctx.pending_keys == {}
    assert "nv-secret-key-1234" not in buf.getvalue()


def test_invalid_key_refused_nothing_held(workspace):
    ctx, _ = make_ctx(workspace, ScriptedPrompter("bad\nkey"))
    with pytest.raises(SetupAborted):
        ask_key(ctx, NOVITA_KEY)
    assert ctx.pending_keys == {} and not (workspace / ".env").exists()


def test_empty_answer_keeps_existing_key(workspace):
    Credentials(workspace, {}).set(NOVITA_KEY, "from-file-123")
    ctx, _ = make_ctx(workspace, ScriptedPrompter(""))
    assert ask_key(ctx, NOVITA_KEY) == "from-file-123"
    assert ctx.pending_keys == {}


def test_env_key_wins_no_prompt(workspace):
    prompter = ScriptedPrompter()
    ctx, buf = make_ctx(workspace, prompter, env={NOVITA_KEY: "from-env-123"})
    assert ask_key(ctx, NOVITA_KEY) == "from-env-123"
    assert prompter.asked == []
    assert ctx.msg("remote.key_from_env", name=NOVITA_KEY) in " ".join(buf.getvalue().split())
    assert "from-env-123" not in buf.getvalue()
    assert not (workspace / ".env").exists()


def test_required_key_missing(workspace):
    ctx, _ = make_ctx(workspace, ScriptedPrompter(""))
    with pytest.raises(SetupAborted):
        ask_key(ctx, NOVITA_KEY)


def test_optional_key_may_be_empty(workspace):
    ctx, _ = make_ctx(workspace, ScriptedPrompter(""))
    assert ask_key(ctx, "SORTINGCAP_OPENAI_API_KEY", optional=True) is None
