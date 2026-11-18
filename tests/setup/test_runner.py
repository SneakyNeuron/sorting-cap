import sys

from app.setup.runner import CommandRunner


def test_capture_logs_command_and_exit_code(workspace):
    runner = CommandRunner(workspace)
    result = runner.run([sys.executable, "-c", "print('hi'); raise SystemExit(3)"])
    assert result.returncode == 3 and result.stdout.strip() == "hi"
    log = (workspace / "logs" / "setup.log").read_text()
    assert "-> exit 3" in log and "print(" in log


def test_missing_binary(workspace):
    result = CommandRunner(workspace).run(["definitely-not-a-binary-xyz"])
    assert result.returncode == 127
    assert "exit 127" in (workspace / "logs" / "setup.log").read_text()


def test_detached_logs_pid_and_writes_output(workspace):
    runner = CommandRunner(workspace)
    result = runner.run([sys.executable, "-c", "print('bg')"], mode="detached", log_name="bg")
    assert result.pid
    assert f"detached pid {result.pid}" in (workspace / "logs" / "setup.log").read_text()
    import time

    for _ in range(50):
        if (workspace / "logs" / "bg.log").read_text().strip():
            break
        time.sleep(0.05)
    assert (workspace / "logs" / "bg.log").read_text().strip() == "bg"


def test_interactive_inherits_terminal(workspace):
    result = CommandRunner(workspace).run(
        [sys.executable, "-c", "raise SystemExit(0)"], mode="interactive"
    )
    assert result.ok and result.stdout == ""
