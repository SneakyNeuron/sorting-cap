import os
import subprocess
import sys

import pytest

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


TRACING_CODE = (
    "import os, app.cli\n"
    "import langsmith.utils as u\n"
    "print(*(os.environ[n] for n in app.cli.TRACING_VARS),"
    " u.tracing_is_enabled(), 'LANGSMITH_GATEWAY' in os.environ)\n"
)


def _run_with(extra: dict[str, str]) -> list[str]:
    env = {k: v for k, v in os.environ.items() if k not in cli.TRACING_VARS}
    env.update(extra)
    result = subprocess.run(
        [sys.executable, "-c", TRACING_CODE],
        env=env,
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
    )
    return result.stdout.split()


@pytest.mark.parametrize(
    "name",
    ["LANGSMITH_TRACING_V2", "LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING"],
)
def test_tracing_forced_off_in_fresh_process(name):
    assert _run_with({name: "true", "LANGSMITH_GATEWAY": "true"}) == [
        "false",
        "false",
        "false",
        "false",
        "False",
        "False",
    ]


def test_tracing_vars_cover_every_name_langsmith_reads():
    assert set(cli.TRACING_VARS) == {
        f"{ns}_{name}" for ns in ("LANGSMITH", "LANGCHAIN") for name in ("TRACING_V2", "TRACING")
    }


def test_usage_in_users_language(capsys, workspace):
    from app.settings import Settings, save_settings

    assert cli.main(["nonsense"]) == 2
    assert capsys.readouterr().out.startswith("použití: sortingcap")
    save_settings(workspace, Settings(language="en"))
    assert cli.main(["nonsense"]) == 2
    assert capsys.readouterr().out.startswith("usage: sortingcap")
