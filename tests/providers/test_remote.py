import stat

import pytest
from helpers import ScriptedPrompter, make_ctx

from app.credentials import NOVITA_KEY
from app.providers.base import SetupAborted
from app.providers.remote import ask_key, require_data_notice


def test_notice_decline_aborts(workspace):
    ctx, buf = make_ctx(workspace, ScriptedPrompter(False))
    with pytest.raises(SetupAborted):
        require_data_notice(ctx)
    assert ctx.msg("remote.notice")[:20] in buf.getvalue()


def test_key_stored_0600_and_masked(workspace):
    ctx, buf = make_ctx(workspace, ScriptedPrompter("nv-secret-key-1234"))
    assert ask_key(ctx, NOVITA_KEY) == "nv-secret-key-1234"
    env = workspace / ".env"
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    assert "nv-secret-key-1234" not in buf.getvalue()


def test_empty_answer_keeps_existing_key(workspace):
    ctx, _ = make_ctx(workspace, ScriptedPrompter(""), env={NOVITA_KEY: "from-env"})
    assert ask_key(ctx, NOVITA_KEY) == "from-env"
    assert not (workspace / ".env").exists()


def test_required_key_missing(workspace):
    ctx, _ = make_ctx(workspace, ScriptedPrompter(""))
    with pytest.raises(SetupAborted):
        ask_key(ctx, NOVITA_KEY)


def test_optional_key_may_be_empty(workspace):
    ctx, _ = make_ctx(workspace, ScriptedPrompter(""))
    assert ask_key(ctx, "SORTINGCAP_OPENAI_API_KEY", optional=True) is None
