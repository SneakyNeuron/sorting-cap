import os
import subprocess
import sys

from app import cli
from app.paths import REPO_ROOT


def test_version_prints_and_exits_zero(capsys):
    assert cli.main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == f"sortingcap {cli.package_version()}"


def test_version_via_console_script():
    result = subprocess.run(
        ["uv", "run", "--offline", "sortingcap", "--version"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("sortingcap ")


def test_unknown_arguments_exit_two(capsys):
    assert cli.main(["nonsense"]) == 2


def test_tracing_forced_off_in_fresh_process():
    env = {
        **os.environ,
        "LANGSMITH_TRACING": "true",
        "LANGCHAIN_TRACING_V2": "true",
        "LANGSMITH_GATEWAY": "true",
    }
    code = (
        "import os, app.cli\n"
        "import langsmith.utils as u\n"
        "print(os.environ['LANGSMITH_TRACING'], os.environ['LANGCHAIN_TRACING_V2'],"
        " u.tracing_is_enabled(), 'LANGSMITH_GATEWAY' in os.environ)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
    )
    assert result.stdout.split() == ["false", "false", "False", "False"]
