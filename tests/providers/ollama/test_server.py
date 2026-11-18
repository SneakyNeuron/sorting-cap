import httpx
import pytest
from helpers import FakeRunner, ScriptedPrompter, hardware, make_ctx

from app.providers.base import SetupAborted
from app.providers.ollama.server import OllamaServer, Status, docker_run_command
from app.setup.runner import CommandResult

BASE = "http://localhost:11434"


class Http:
    """Fake Ollama HTTP: `states` is consumed per /api/version call (last repeats)."""

    def __init__(self, *states):
        self.states = list(states)

    def __call__(self, request):
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        if state == "down":
            raise httpx.ConnectError("refused", request=request)
        if state == "other":
            return httpx.Response(404, text="nginx")
        return httpx.Response(200, json={"version": "0.13.0"})


def server(workspace, http, prompter=None, runner=None, which=None, free=lambda p: True):
    ctx, buf = make_ctx(workspace, prompter, runner=runner or FakeRunner(), handler=http)
    srv = OllamaServer(
        ctx,
        which=which or (lambda n: None),
        sleep=lambda s: None,
        port_is_free=free,
        ready_timeout=3,
        poll_interval=1,
    )
    return srv, ctx, buf


def test_detect_running(workspace):
    srv, *_ = server(workspace, Http("up"))
    d = srv.detect(BASE)
    assert d.status is Status.RUNNING and d.version == "0.13.0"


def test_detect_other_process(workspace):
    assert server(workspace, Http("other"))[0].detect(BASE).status is Status.OTHER


def test_detect_stopped_binary_with_systemd(workspace):
    runner = FakeRunner(lambda a, m: CommandResult(0, "ollama.service enabled enabled\n"))
    srv, *_ = server(
        workspace,
        Http("down"),
        runner=runner,
        which=lambda n: "/usr/bin/ollama" if n == "ollama" else None,
    )
    d = srv.detect(BASE)
    assert d.status is Status.STOPPED_BINARY and d.systemd


def test_detect_stopped_container(workspace):
    runner = FakeRunner(lambda a, m: CommandResult(0, "exited\n"))
    srv, *_ = server(
        workspace,
        Http("down"),
        runner=runner,
        which=lambda n: "/usr/bin/docker" if n == "docker" else None,
    )
    assert srv.detect(BASE).status is Status.STOPPED_CONTAINER


def test_detect_nothing(workspace):
    assert server(workspace, Http("down"))[0].detect(BASE).status is Status.NOTHING


def test_start_binary_detached(workspace):
    runner = FakeRunner(lambda a, m: CommandResult(0, ""))
    srv, *_ = server(
        workspace,
        Http("down", "down", "up"),
        ScriptedPrompter(True),
        runner,
        which=lambda n: "/usr/bin/ollama" if n == "ollama" else None,
    )
    assert srv.ensure(hardware(), BASE) == BASE
    assert (["ollama", "serve"], "detached") in runner.calls


def test_start_systemd_interactive(workspace):
    runner = FakeRunner(lambda a, m: CommandResult(0, "ollama.service enabled\n"))
    srv, *_ = server(
        workspace,
        Http("down", "up"),
        ScriptedPrompter(True),
        runner,
        which=lambda n: "/usr/bin/ollama" if n == "ollama" else None,
    )
    srv.ensure(hardware(), BASE)
    assert (["sudo", "systemctl", "start", "ollama"], "interactive") in runner.calls


def test_start_container(workspace):
    runner = FakeRunner(lambda a, m: CommandResult(0, "exited\n"))
    srv, *_ = server(
        workspace,
        Http("down", "up"),
        ScriptedPrompter(True),
        runner,
        which=lambda n: "/usr/bin/docker" if n == "docker" else None,
    )
    srv.ensure(hardware(), BASE)
    assert ["docker", "start", "ollama"] in [c for c, _ in runner.calls]


@pytest.mark.parametrize("gpu", [False, True])
def test_docker_install_bound_to_loopback(workspace, gpu):
    runner = FakeRunner()
    srv, ctx, buf = server(workspace, Http("down", "up"), ScriptedPrompter("docker", True), runner)
    assert srv.ensure(hardware(gpu=gpu), BASE) == BASE
    cmd = runner.calls[-1][0]
    assert cmd == docker_run_command(11434, gpu=gpu)
    assert "127.0.0.1:11434:11434" in cmd
    assert ("--gpus=all" in cmd) is gpu
    assert " ".join(cmd) in buf.getvalue()  # shown in full before confirming


def test_docker_default_when_usable_native_otherwise(workspace):
    p = ScriptedPrompter("back")
    srv, *_ = server(workspace, Http("down"), p)
    with pytest.raises(SetupAborted):
        srv.ensure(hardware(), BASE)
    assert p.choose_calls()[0]["default"] == "docker"
    p = ScriptedPrompter("back")
    srv, *_ = server(workspace, Http("down"), p)
    with pytest.raises(SetupAborted):
        srv.ensure(hardware(docker=False, usable=False), BASE)
    assert p.choose_calls()[0]["default"] == "native"
    assert [c.value for c in p.choose_calls()[0]["choices"]] == ["native", "back"]


def test_port_taken_uses_next_free_port(workspace):
    runner = FakeRunner()
    srv, *_ = server(
        workspace, Http("other", "up"), ScriptedPrompter(True), runner, free=lambda p: p >= 11436
    )
    assert srv.ensure(hardware(), BASE) == "http://localhost:11436"
    assert "127.0.0.1:11436:11434" in runner.calls[-1][0]


def test_port_taken_without_docker(workspace):
    srv, *_ = server(workspace, Http("other"))
    with pytest.raises(SetupAborted):
        srv.ensure(hardware(docker=False), BASE)


def test_native_install(workspace):
    runner = FakeRunner()
    srv, ctx, buf = server(workspace, Http("down", "up"), ScriptedPrompter("native", True), runner)
    srv.ensure(hardware(docker=False), BASE)
    assert runner.calls[-1] == (
        ["sh", "-c", "curl -fsSL https://ollama.com/install.sh | sh"],
        "interactive",
    )
    assert "curl -fsSL https://ollama.com/install.sh | sh" in buf.getvalue()


@pytest.mark.parametrize("answers", [("docker", False), ("native", False), ("back",)])
def test_declining_runs_nothing(workspace, answers):
    runner = FakeRunner()
    srv, *_ = server(workspace, Http("down"), ScriptedPrompter(*answers), runner)
    with pytest.raises(SetupAborted):
        srv.ensure(hardware(), BASE)
    assert runner.calls == []


def test_declining_start(workspace):
    runner = FakeRunner(lambda a, m: CommandResult(0, ""))
    srv, *_ = server(
        workspace,
        Http("down"),
        ScriptedPrompter(False),
        runner,
        which=lambda n: "/usr/bin/ollama" if n == "ollama" else None,
    )
    with pytest.raises(SetupAborted):
        srv.ensure(hardware(), BASE)
    assert all(c[0][0] != "ollama" for c in runner.calls)


def test_docker_permission_error(workspace):
    runner = FakeRunner(
        lambda a, m: CommandResult(
            1, "", "permission denied while trying to connect to the Docker daemon"
        )
    )
    srv, ctx, _ = server(workspace, Http("down"), ScriptedPrompter("docker", True), runner)
    with pytest.raises(SetupAborted) as exc:
        srv.ensure(hardware(), BASE)
    assert "docker" in exc.value.message and "usermod" in exc.value.message


def test_ready_timeout(workspace):
    srv, *_ = server(workspace, Http("down"), ScriptedPrompter("docker", True))
    with pytest.raises(SetupAborted) as exc:
        srv.ensure(hardware(), BASE)
    assert "3" in exc.value.message
