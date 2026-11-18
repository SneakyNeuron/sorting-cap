import json

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


class Docker:
    """A stateful fake Docker with at most one `ollama` container."""

    def __init__(self, state=None, port=None):
        self.state, self.port = state, port
        self.runs = 0

    def __call__(self, args, mode):
        if args[:3] == ["docker", "container", "inspect"]:
            if self.state is None:
                return CommandResult(1, "", "Error: No such container: ollama")
            bindings = (
                {"11434/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(self.port)}]}
                if self.port
                else {}
            )
            return CommandResult(0, f"{self.state} {json.dumps(bindings)}\n")
        if args[:2] == ["docker", "run"]:
            if self.state is not None:
                return CommandResult(125, "", 'Conflict. The container name "/ollama" is in use')
            self.runs += 1
            self.state = "running"
            self.port = int(args[args.index("-p") + 1].split(":")[1])
            return CommandResult(0, "abc123\n")
        if args == ["docker", "start", "ollama"]:
            self.state = "running"
            return CommandResult(0, "ollama\n")
        return CommandResult(0, "")


def docker_http(docker, taken=11434, pulls=None):
    """11434 is taken by something else; the container's port answers while it runs."""

    def handler(request):
        port = request.url.port
        if port == taken:
            return httpx.Response(404, text="nginx")
        if docker.state != "running" or port != docker.port:
            raise httpx.ConnectError("refused", request=request)
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.13.0"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": []})
        outcome = pulls.pop(0) if pulls else "ok"
        if outcome == "fail":
            return httpx.Response(200, content=b'{"error": "network down"}\n')
        return httpx.Response(200, content=b'{"status": "success"}\n')

    return handler


def docker_server(workspace, docker, http, prompter):
    runner = FakeRunner(docker)
    srv, ctx, buf = server(
        workspace,
        http,
        prompter,
        runner,
        which=lambda n: "/usr/bin/docker" if n == "docker" else None,
        free=lambda p: p >= 11435,
    )
    return srv, ctx, buf, runner


def test_retry_after_failed_pull_on_alternate_port_reuses_container(workspace):
    from app.providers.ollama.provider import OllamaProvider

    docker = Docker()
    http = docker_http(docker, pulls=["fail", "ok"])
    tag = "qwen3.5:4b-q4_K_M"
    # first round: docker run on 11435, then the pull fails; second round: no new docker run
    prompter = ScriptedPrompter(True, tag, tag)
    srv, ctx, buf, runner = docker_server(workspace, docker, http, prompter)
    provider = OllamaProvider(server_factory=lambda c: srv)
    with pytest.raises(SetupAborted):
        provider.run_setup(ctx, hardware(ram=8))
    assert ctx.settings.ollama_base_url == BASE  # nothing saved after the failure
    result = provider.run_setup(ctx, hardware(ram=8))
    assert result.updates == {"ollama_base_url": "http://localhost:11435"}
    assert docker.runs == 1
    assert [c for c, _ in runner.calls if c[:2] == ["docker", "run"]] == [docker_run_command(11435)]
    assert ctx.msg("ollama.container_running", url="http://localhost:11435") in buf.getvalue()


def test_port_taken_stopped_container_on_other_port_is_started(workspace):
    docker = Docker("exited", 11435)
    srv, *_, runner = docker_server(workspace, docker, docker_http(docker), ScriptedPrompter(True))
    assert srv.ensure(hardware(), BASE) == "http://localhost:11435"
    assert ["docker", "start", "ollama"] in [c for c, _ in runner.calls]
    assert docker.runs == 0


def test_port_taken_container_without_free_port_fails_clearly(workspace):
    for port in (None, 11434):
        docker = Docker("exited", port)
        srv, ctx, _, runner = docker_server(workspace, docker, docker_http(docker), None)
        with pytest.raises(SetupAborted) as exc:
            srv.ensure(hardware(), BASE)
        assert exc.value.message == ctx.msg("ollama.container_conflict")
        assert all(c[:2] not in (["docker", "run"], ["docker", "start"]) for c, _ in runner.calls)


def test_down_url_with_container_on_other_port_uses_that_port(workspace):
    docker = Docker("running", 11435)

    def http(request):
        if request.url.port == 11435:
            return httpx.Response(200, json={"version": "0.13.0"})
        raise httpx.ConnectError("refused", request=request)

    srv, *_ = docker_server(workspace, docker, http, None)
    assert srv.ensure(hardware(), BASE) == "http://localhost:11435"


def test_parse_container():
    from app.providers.ollama.server import Container, parse_container

    assert parse_container("") is None
    assert parse_container("exited null") == Container("exited", None)
    assert parse_container(
        'running {"11434/tcp":[{"HostIp":"127.0.0.1","HostPort":"11436"}]}'
    ) == Container("running", 11436)


@pytest.mark.parametrize(
    ("url", "host"),
    [
        ("http://localhost:11434", "127.0.0.1:11434"),
        ("http://localhost:11435", "127.0.0.1:11435"),
        ("http://127.0.0.2", "127.0.0.2:11434"),
        ("http://[::1]:11500", "[::1]:11500"),
    ],
)
def test_ollama_serve_listens_on_the_configured_port(workspace, url, host):
    from app.providers.ollama.server import ollama_host

    assert ollama_host(url) == host
    runner = FakeRunner(lambda a, m: CommandResult(0, ""))
    srv, ctx, buf = server(
        workspace,
        Http("down", "up"),
        ScriptedPrompter(True),
        runner,
        which=lambda n: "/usr/bin/ollama" if n == "ollama" else None,
    )
    assert srv.ensure(hardware(), url) == url
    i = runner.calls.index((["ollama", "serve"], "detached"))
    assert runner.envs[i] == {"OLLAMA_HOST": host}
    assert f"$ OLLAMA_HOST={host} ollama serve" in buf.getvalue()


@pytest.mark.parametrize("state", ["down", "other"])
def test_remote_url_not_answering_never_offers_local_install(workspace, state):
    runner = FakeRunner()
    prompter = ScriptedPrompter()
    srv, ctx, _ = server(
        workspace,
        Http(state),
        prompter,
        runner,
        which=lambda n: f"/usr/bin/{n}",
    )
    url = "http://192.168.1.20:11434"
    with pytest.raises(SetupAborted) as exc:
        srv.ensure(hardware(), url)
    assert exc.value.message == ctx.msg("ollama.remote_unreachable", url=url)
    assert runner.calls == [] and prompter.asked == []


def test_remote_url_running_is_used(workspace):
    srv, *_ = server(workspace, Http("up"))
    assert srv.ensure(hardware(), "http://192.168.1.20:11434") == "http://192.168.1.20:11434"
