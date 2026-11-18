"""Find, start or install a local Ollama server (https://docs.ollama.com/api).

Every command goes through `app/setup/runner.py` and is shown in full before
the user confirms it.
"""

from __future__ import annotations

import enum
import json
import shlex
import shutil
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from app.providers.base import SetupAborted
from app.providers.hosts import is_loopback_url
from app.setup.hardware import Hardware, is_docker_permission_error
from app.tools.context import Choice, ToolContext

DEFAULT_PORT = 11434
INSTALL_SCRIPT = "curl -fsSL https://ollama.com/install.sh | sh"


class Status(enum.Enum):
    RUNNING = "running"
    OTHER = "other"  # something else answers on the port
    STOPPED_BINARY = "stopped_binary"
    # an `ollama` container exists (stopped, or running on another port)
    STOPPED_CONTAINER = "stopped_container"
    NOTHING = "nothing"


@dataclass(frozen=True)
class Container:
    """An existing `ollama` Docker container: its state and loopback host port."""

    state: str
    port: int | None = None

    @property
    def running(self) -> bool:
        return self.state == "running"


@dataclass(frozen=True)
class Detection:
    status: Status
    version: str | None = None
    systemd: bool = False
    container: Container | None = None


def docker_run_command(port: int = DEFAULT_PORT, gpu: bool = False) -> list[str]:
    """T6: the Ollama container, bound to loopback only."""
    cmd = [
        "docker",
        "run",
        "-d",
        "--name",
        "ollama",
        "-v",
        "ollama:/root/.ollama",
        "-p",
        f"127.0.0.1:{port}:11434",
    ]
    if gpu:
        cmd.append("--gpus=all")
    cmd.append("ollama/ollama")
    return cmd


CONTAINER_INSPECT = [
    "docker",
    "container",
    "inspect",
    "--format",
    "{{.State.Status}} {{json .HostConfig.PortBindings}}",
    "ollama",
]


def parse_container(stdout: str) -> Container | None:
    """Parse `CONTAINER_INSPECT` output: `<state> <port bindings as JSON>`."""
    state, _, bindings = stdout.strip().partition(" ")
    if not state:
        return None
    port = None
    try:
        data = json.loads(bindings) if bindings else None
    except ValueError:
        data = None
    if isinstance(data, dict):
        for binding in data.get("11434/tcp") or []:
            host_port = str((binding or {}).get("HostPort", ""))
            if host_port.isdigit():
                port = int(host_port)
                break
    return Container(state, port)


def ollama_host(base_url: str) -> str:
    """`OLLAMA_HOST` value that makes `ollama serve` listen where `base_url` points."""
    parts = urlsplit(base_url)
    host = parts.hostname or "127.0.0.1"
    if host == "localhost":
        host = "127.0.0.1"
    elif ":" in host:
        host = f"[{host}]"
    return f"{host}:{parts.port or DEFAULT_PORT}"


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


class OllamaServer:
    def __init__(
        self,
        ctx: ToolContext,
        *,
        which: Callable[[str], str | None] = shutil.which,
        sleep: Callable[[float], None] = time.sleep,
        port_is_free: Callable[[int], bool] = _port_is_free,
        ready_timeout: float = 60.0,
        poll_interval: float = 1.0,
    ) -> None:
        self.ctx = ctx
        self.which = which
        self.sleep = sleep
        self.port_is_free = port_is_free
        self.ready_timeout = ready_timeout
        self.poll_interval = poll_interval

    # --- detection ---------------------------------------------------------

    def probe(self, base_url: str) -> tuple[str, str | None]:
        """("running", version) | ("other", None) | ("down", None)."""
        try:
            with self.ctx.http_client(timeout=3) as client:
                response = client.get(f"{base_url}/api/version")
        except httpx.TransportError:
            return "down", None
        try:
            data = response.json()
        except ValueError:
            data = None
        if response.status_code == 200 and isinstance(data, dict) and "version" in data:
            return "running", str(data["version"])
        return "other", None

    def detect(self, base_url: str) -> Detection:
        state, version = self.probe(base_url)
        if state == "running":
            return Detection(Status.RUNNING, version=version)
        if state == "other":
            return Detection(Status.OTHER)
        if self.which("ollama"):
            units = self.ctx.runner.run(
                ["systemctl", "list-unit-files", "ollama.service", "--no-legend"], timeout=10
            )
            return Detection(
                Status.STOPPED_BINARY, systemd=units.ok and "ollama.service" in units.stdout
            )
        container = self.find_container()
        if container is not None:
            return Detection(Status.STOPPED_CONTAINER, container=container)
        return Detection(Status.NOTHING)

    def find_container(self) -> Container | None:
        """The existing `ollama` container (any state), or None (no Docker, no container)."""
        if not self.which("docker"):
            return None
        result = self.ctx.runner.run(CONTAINER_INSPECT, timeout=20)
        return parse_container(result.stdout) if result.ok else None

    # --- actions -----------------------------------------------------------

    def _confirm_command(self, command: str) -> None:
        ctx = self.ctx
        ctx.console.print(f"$ {command}", style="bold", highlight=False, markup=False)
        if not ctx.confirm(ctx.msg("ollama.confirm_command")):
            raise SetupAborted(ctx.msg("setup.declined"))

    def wait_ready(self, base_url: str) -> str:
        ctx = self.ctx
        ctx.say("ollama.waiting", url=base_url)
        waited = 0.0
        while True:
            state, version = self.probe(base_url)
            if state == "running":
                ctx.say("ollama.ready", style="green", version=version or "?")
                return base_url
            if waited >= self.ready_timeout:
                raise SetupAborted(
                    ctx.msg("ollama.timeout", url=base_url, seconds=int(self.ready_timeout))
                )
            self.sleep(self.poll_interval)
            waited += self.poll_interval

    def _docker_failure(self, stderr: str) -> SetupAborted:
        ctx = self.ctx
        if is_docker_permission_error(stderr):
            return SetupAborted(ctx.msg("ollama.docker_permission"))
        return SetupAborted(ctx.msg("ollama.command_failed", detail=stderr.strip()[:300] or "?"))

    def start_binary(self, systemd: bool, base_url: str) -> str:
        if systemd:
            cmd = ["sudo", "systemctl", "start", "ollama"]
            self._confirm_command(shlex.join(cmd))
            result = self.ctx.runner.run(cmd, mode="interactive", timeout=120)
        else:
            cmd = ["ollama", "serve"]
            env = {"OLLAMA_HOST": ollama_host(base_url)}
            self._confirm_command(
                " ".join([*(f"{k}={v}" for k, v in env.items()), shlex.join(cmd)])
            )
            result = self.ctx.runner.run(cmd, mode="detached", log_name="ollama-serve", env=env)
        if not result.ok:
            raise SetupAborted(
                self.ctx.msg("ollama.command_failed", detail=result.stderr.strip() or "?")
            )
        return self.wait_ready(base_url)

    def start_container(self, base_url: str) -> str:
        cmd = ["docker", "start", "ollama"]
        self._confirm_command(shlex.join(cmd))
        result = self.ctx.runner.run(cmd, timeout=120)
        if not result.ok:
            raise self._docker_failure(result.stderr)
        return self.wait_ready(base_url)

    def reuse_container(self, container: Container, base_url: str) -> str:
        """Use the existing `ollama` container on the port it is published on.

        A container created on another port by an earlier, unfinished `-setup` is
        found here again instead of a second `docker run` clashing on its name.
        """
        url = base_url
        if container.port and container.port != self._port_of(base_url):
            url = f"http://localhost:{container.port}"
        if container.running:
            self.ctx.say("ollama.container_running", url=url)
            return self.wait_ready(url)
        self.ctx.say("ollama.stopped_container")
        return self.start_container(url)

    def next_free_port(self, start: int = DEFAULT_PORT + 1) -> int:
        for port in range(start, start + 100):
            if self.port_is_free(port):
                return port
        raise SetupAborted(self.ctx.msg("ollama.no_free_port"))

    def install_docker(self, hw: Hardware, port: int) -> str:
        cmd = docker_run_command(port, gpu=hw.docker_gpu)
        self._confirm_command(shlex.join(cmd))
        self.ctx.say("ollama.docker_running")
        result = self.ctx.runner.run(cmd, timeout=None)
        if not result.ok:
            raise self._docker_failure(result.stderr)
        return self.wait_ready(f"http://localhost:{port}")

    def install_native(self, base_url: str) -> str:
        self._confirm_command(INSTALL_SCRIPT)
        result = self.ctx.runner.shell(INSTALL_SCRIPT, mode="interactive")
        if not result.ok:
            raise SetupAborted(
                self.ctx.msg("ollama.command_failed", detail=f"exit {result.returncode}")
            )
        return self.wait_ready(base_url)

    def _ensure_remote(self, base_url: str) -> str:
        """A non-loopback URL is used as is: never a local start or install for it."""
        ctx = self.ctx
        state, version = self.probe(base_url)
        if state == "running":
            ctx.say("ollama.found", style="green", url=base_url, version=version or "?")
            return base_url
        raise SetupAborted(ctx.msg("ollama.remote_unreachable", url=base_url))

    def _port_of(self, base_url: str) -> int:
        return urlsplit(base_url).port or DEFAULT_PORT

    def ensure(self, hw: Hardware, base_url: str) -> str:
        """Return a base URL with a running Ollama, or raise `SetupAborted`."""
        ctx = self.ctx
        if not is_loopback_url(base_url):
            return self._ensure_remote(base_url)
        detection = self.detect(base_url)
        if detection.status is Status.RUNNING:
            ctx.say("ollama.found", style="green", url=base_url, version=detection.version or "?")
            return base_url
        if detection.status is Status.OTHER:
            ctx.say("ollama.port_taken", style="yellow", url=base_url)
            if not hw.docker:
                raise SetupAborted(ctx.msg("ollama.port_taken_no_docker"))
            container = self.find_container()
            if container is not None:
                if not container.port or container.port == self._port_of(base_url):
                    raise SetupAborted(ctx.msg("ollama.container_conflict"))
                return self.reuse_container(container, base_url)
            return self.install_docker(hw, self.next_free_port(self._port_of(base_url) + 1))
        if detection.status is Status.STOPPED_BINARY:
            ctx.say("ollama.stopped_binary")
            return self.start_binary(detection.systemd, base_url)
        if detection.status is Status.STOPPED_CONTAINER and detection.container is not None:
            return self.reuse_container(detection.container, base_url)
        ctx.say("ollama.not_found")
        choices = [Choice("native", ctx.msg("ollama.route_native"))]
        if hw.docker:
            choices.insert(0, Choice("docker", ctx.msg("ollama.route_docker")))
        choices.append(Choice("back", ctx.msg("setup.back")))
        default = "docker" if hw.docker and hw.docker_usable else "native"
        route = ctx.choose(ctx.msg("ollama.route_question"), choices, default=default)
        if route == "back":
            raise SetupAborted(ctx.msg("setup.declined"))
        if route == "docker":
            return self.install_docker(hw, self._port_of(base_url))
        return self.install_native(base_url)
