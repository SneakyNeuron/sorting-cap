"""Hardware detection: RAM, NVIDIA VRAM, Docker and Docker GPU support.

Sizes are rounded to the nearest whole GB (T5): a 24 GB card reports about
23.99 GiB, a 16 GB machine about 15.5 GiB.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.setup.runner import CommandRunner


@dataclass(frozen=True)
class Hardware:
    ram_gb: int
    vram_gb: int = 0
    gpu_name: str | None = None
    docker: bool = False
    docker_usable: bool = False
    docker_gpu: bool = False
    docker_error: str | None = None


def kib_to_gb(kib: int) -> int:
    return round(kib / 1024 / 1024)


def mib_to_gb(mib: float) -> int:
    return round(mib / 1024)


def read_ram_gb(meminfo: Path = Path("/proc/meminfo")) -> int:
    try:
        for line in meminfo.read_text().splitlines():
            if line.startswith("MemTotal:"):
                return kib_to_gb(int(line.split()[1]))
    except (OSError, ValueError, IndexError):
        pass
    return 0


def detect_gpu(runner: CommandRunner, which: Callable[[str], str | None] = shutil.which):
    """(largest VRAM in GB, its name), or (0, None) without nvidia-smi."""
    if not which("nvidia-smi"):
        return 0, None
    result = runner.run(
        ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"], timeout=15
    )
    if not result.ok:
        return 0, None
    best: tuple[float, str | None] = (0.0, None)
    for line in result.stdout.splitlines():
        name, _, mem = line.rpartition(",")
        try:
            mib = float(mem.strip())
        except ValueError:
            continue
        if mib > best[0]:
            best = (mib, name.strip() or None)
    return mib_to_gb(best[0]), best[1]


def detect_docker(runner: CommandRunner, which: Callable[[str], str | None] = shutil.which):
    """(installed, usable, gpu support, error text)."""
    if not which("docker"):
        return False, False, False, None
    result = runner.run(["docker", "info", "--format", "{{json .Runtimes}}"], timeout=20)
    if not result.ok:
        return True, False, False, (result.stderr or result.stdout).strip() or None
    return True, True, "nvidia" in result.stdout, None


def detect(
    runner: CommandRunner,
    *,
    meminfo: Path = Path("/proc/meminfo"),
    which: Callable[[str], str | None] = shutil.which,
) -> Hardware:
    vram, gpu = detect_gpu(runner, which)
    docker, usable, docker_gpu, error = detect_docker(runner, which)
    return Hardware(
        ram_gb=read_ram_gb(meminfo),
        vram_gb=vram,
        gpu_name=gpu,
        docker=docker,
        docker_usable=usable,
        docker_gpu=docker_gpu,
        docker_error=error,
    )


def is_docker_permission_error(text: str | None) -> bool:
    return text is not None and "permission denied" in text.lower()
