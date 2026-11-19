"""Installed models (`GET /api/tags`) and streamed pulls (`POST /api/pull`).

Ollama API reference: https://docs.ollama.com/api
"""

from __future__ import annotations

import json

import httpx

from app.providers.base import SetupAborted
from app.tools.context import ToolContext


def installed_models(ctx: ToolContext, base_url: str) -> set[str]:
    try:
        with ctx.http_client(timeout=10) as client:
            response = client.get(f"{base_url}/api/tags")
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError):
        return set()
    names: set[str] = set()
    for model in data.get("models", []):
        names.update(n for n in (model.get("name"), model.get("model")) if n)
    return names


def pull_model(ctx: ToolContext, base_url: str, tag: str) -> None:
    from rich.progress import BarColumn, DownloadColumn, Progress, TextColumn, TransferSpeedColumn

    if tag in installed_models(ctx, base_url):
        ctx.say("ollama.pull_skip", model=tag)
        return
    ctx.say("ollama.pull_start", model=tag)
    success = False
    try:
        with (
            ctx.http_client(timeout=None) as client,
            client.stream(
                "POST", f"{base_url}/api/pull", json={"model": tag, "stream": True}
            ) as response,
            Progress(
                TextColumn("{task.description}", markup=False),
                BarColumn(),
                DownloadColumn(),
                TransferSpeedColumn(),
                console=ctx.console,
                transient=True,
            ) as progress,
        ):
            response.raise_for_status()
            task = progress.add_task(tag, total=None)
            for line in response.iter_lines():
                if not line.strip():
                    continue
                data = json.loads(line)
                if "error" in data:
                    raise SetupAborted(
                        ctx.msg("ollama.pull_failed", model=tag, detail=data["error"])
                    )
                status = str(data.get("status", ""))
                if "total" in data:
                    progress.update(
                        task,
                        total=data["total"],
                        completed=data.get("completed", 0),
                        description=f"{tag} {status[:30]}",
                    )
                else:
                    progress.update(task, description=f"{tag} {status[:30]}")
                if status == "success":
                    success = True
    except KeyboardInterrupt as exc:
        raise SetupAborted(ctx.msg("ollama.pull_interrupted", model=tag)) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise SetupAborted(ctx.msg("ollama.pull_failed", model=tag, detail=str(exc)[:200])) from exc
    if not success:
        raise SetupAborted(ctx.msg("ollama.pull_interrupted", model=tag))
    ctx.say("ollama.pull_done", style="green", model=tag)
