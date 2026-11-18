import json

import httpx
import pytest
from helpers import make_ctx

from app.providers.base import SetupAborted
from app.providers.ollama.pull import pull_model

BASE = "http://localhost:11434"
TAG = "qwen3.5:9b-q4_K_M"


def http(tags=(), lines=None, error=None):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": t, "model": t} for t in tags]})
        if error:
            raise error
        body = "".join(json.dumps(line) + "\n" for line in lines)
        return httpx.Response(200, content=body.encode())

    return handler, calls


def test_skip_when_installed(workspace):
    handler, calls = http(tags=[TAG])
    ctx, buf = make_ctx(workspace, handler=handler)
    pull_model(ctx, BASE, TAG)
    assert calls == ["/api/tags"]
    assert "already" in buf.getvalue() or "už" in buf.getvalue()


def test_progress_to_success(workspace):
    lines = [
        {"status": "pulling manifest"},
        {"status": "pulling abc", "total": 100, "completed": 50},
        {"status": "pulling abc", "total": 100, "completed": 100},
        {"status": "success"},
    ]
    handler, calls = http(lines=lines)
    ctx, _ = make_ctx(workspace, handler=handler)
    pull_model(ctx, BASE, TAG)
    assert calls == ["/api/tags", "/api/pull"]


def test_markup_like_status_shown_literally(workspace):
    import io

    from rich.console import Console

    lines = [
        {"status": "pulling [/x]", "total": 100, "completed": 100},
        {"status": "success"},
    ]
    handler, _ = http(lines=lines)
    ctx, _ = make_ctx(workspace, handler=handler)
    buf = io.StringIO()
    ctx.console = Console(file=buf, force_terminal=True, color_system=None, width=120)
    pull_model(ctx, BASE, "odd[/x]:1")  # the progress bar renders tag and status as text
    assert "odd[/x]:1 success" in buf.getvalue()


def test_interrupted_stream(workspace):
    handler, _ = http(lines=[{"status": "pulling abc", "total": 100, "completed": 10}])
    ctx, _ = make_ctx(workspace, handler=handler)
    with pytest.raises(SetupAborted):
        pull_model(ctx, BASE, TAG)


def test_network_error(workspace):
    handler, _ = http(error=httpx.ReadError("reset"))
    ctx, _ = make_ctx(workspace, handler=handler)
    with pytest.raises(SetupAborted):
        pull_model(ctx, BASE, TAG)


def test_ctrl_c_reported(workspace):
    handler, _ = http(error=KeyboardInterrupt())
    ctx, _ = make_ctx(workspace, handler=handler)
    with pytest.raises(SetupAborted) as exc:
        pull_model(ctx, BASE, TAG)
    assert TAG in exc.value.message


def test_error_line(workspace):
    handler, _ = http(lines=[{"error": "pull model manifest: file does not exist"}])
    ctx, _ = make_ctx(workspace, handler=handler)
    with pytest.raises(SetupAborted, match="does not exist"):
        pull_model(ctx, BASE, TAG)
