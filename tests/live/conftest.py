"""Opt-in live suite (T12): real models, no fakes. Run with `uv run pytest -m live`.

The root conftest points `SORTINGCAP_WORKSPACE` and `HOME` at a temporary directory for
every test, so the real workspace (where `/setup` installed the classifier) is resolved
here, at import time, before any test runs. `SORTINGCAP_LIVE_WORKSPACE` overrides it.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.workspace import resolve_workspace

REAL_WORKSPACE: Path = Path(
    os.environ.get("SORTINGCAP_LIVE_WORKSPACE") or resolve_workspace(dict(os.environ))
).expanduser()


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    here = Path(__file__).parent
    for item in items:
        if here in Path(item.fspath).parents:
            item.add_marker(pytest.mark.live)


@pytest.fixture(scope="session")
def real_workspace() -> Path:
    return REAL_WORKSPACE
