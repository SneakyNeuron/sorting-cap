from helpers import FakeRunner

from app.setup.hardware import detect, kib_to_gb, mib_to_gb, read_ram_gb
from app.setup.runner import CommandResult


def test_rounding_of_reported_sizes():
    assert mib_to_gb(24564) == 24
    assert mib_to_gb(8188) == 8
    assert mib_to_gb(16376) == 16
    assert kib_to_gb(16_256_000) == 16  # ~15.5 GiB machine
    assert kib_to_gb(7_990_000) == 8


def test_read_meminfo(tmp_path):
    p = tmp_path / "meminfo"
    p.write_text("MemTotal:       32_000_000 kB\n".replace("_", ""))
    assert read_ram_gb(p) == 31
    p.write_text("MemTotal:       32765432 kB\nMemFree: 1 kB\n")
    assert read_ram_gb(p) == 31  # 31.25 GiB
    assert read_ram_gb(tmp_path / "missing") == 0


def test_detect_gpu_and_docker(tmp_path):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal: 65842292 kB\n")

    def respond(args, mode):
        if args[0] == "nvidia-smi":
            return CommandResult(0, "NVIDIA GeForce RTX 3060, 12288\nNVIDIA RTX 4090, 24564\n")
        if args[:2] == ["docker", "info"]:
            return CommandResult(
                0, '{"io.containerd.runc.v2":{},"nvidia":{"path":"nvidia-container-runtime"}}'
            )
        return CommandResult(1)

    hw = detect(FakeRunner(respond), meminfo=meminfo, which=lambda name: f"/usr/bin/{name}")
    assert (hw.ram_gb, hw.vram_gb, hw.gpu_name) == (63, 24, "NVIDIA RTX 4090")
    assert hw.docker and hw.docker_usable and hw.docker_gpu


def test_no_gpu_no_docker(tmp_path):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal: 16256000 kB\n")
    runner = FakeRunner()
    hw = detect(runner, meminfo=meminfo, which=lambda name: None)
    assert (hw.ram_gb, hw.vram_gb, hw.docker) == (16, 0, False)
    assert runner.calls == []


def test_docker_permission_denied(tmp_path):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal: 16256000 kB\n")
    runner = FakeRunner(
        lambda a, m: CommandResult(1, "", "permission denied while trying to connect")
    )
    hw = detect(
        runner, meminfo=meminfo, which=lambda n: "/usr/bin/docker" if n == "docker" else None
    )
    assert hw.docker and not hw.docker_usable and "permission denied" in hw.docker_error
