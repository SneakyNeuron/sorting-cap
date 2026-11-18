"""The one place that runs external commands (the only `subprocess` user).

Every command line and its exit code (or PID, for detached commands) is logged
to `<workspace>/logs/setup.log`. No command line ever carries a key.
"""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

Mode = Literal["capture", "interactive", "detached"]


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""
    pid: int | None = None

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class CommandRunner:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    @property
    def log_dir(self) -> Path:
        return self.workspace / "logs"

    def _log(self, args: list[str], outcome: str) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).isoformat(timespec="seconds")
        with (self.log_dir / "setup.log").open("a", encoding="utf-8") as fh:
            fh.write(f"{stamp} {shlex.join(args)} -> {outcome}\n")

    def run(
        self,
        args: list[str],
        *,
        mode: Mode = "capture",
        timeout: float | None = 60,
        log_name: str | None = None,
    ) -> CommandResult:
        """Run `args`.

        - capture: output returned, no terminal access.
        - interactive: inherits the terminal (a sudo prompt is visible); stop any
          spinner before calling.
        - detached: new session, output to `logs/<log_name>.log`, outlives the app.
        """
        if mode == "detached":
            self.log_dir.mkdir(parents=True, exist_ok=True)
            out_path = self.log_dir / f"{log_name or Path(args[0]).name}.log"
            try:
                with out_path.open("ab") as out:
                    proc = subprocess.Popen(
                        args,
                        stdin=subprocess.DEVNULL,
                        stdout=out,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
            except OSError as exc:
                self._log(args, f"error {exc.strerror or exc}")
                return CommandResult(127, stderr=str(exc))
            self._log(args, f"detached pid {proc.pid}")
            return CommandResult(0, pid=proc.pid)
        try:
            if mode == "interactive":
                proc = subprocess.run(args, timeout=timeout, check=False)
                result = CommandResult(proc.returncode)
            else:
                proc = subprocess.run(
                    args, capture_output=True, text=True, timeout=timeout, check=False
                )
                result = CommandResult(proc.returncode, proc.stdout, proc.stderr)
        except FileNotFoundError as exc:
            result = CommandResult(127, stderr=str(exc))
        except subprocess.TimeoutExpired:
            result = CommandResult(124, stderr="timeout")
        except OSError as exc:
            result = CommandResult(126, stderr=str(exc))
        self._log(args, f"exit {result.returncode}")
        return result

    def shell(
        self, script: str, *, mode: Mode = "interactive", timeout: float | None = None
    ) -> CommandResult:
        """Run a shell pipeline (only the official Ollama install script needs this)."""
        return self.run(["sh", "-c", script], mode=mode, timeout=timeout)
