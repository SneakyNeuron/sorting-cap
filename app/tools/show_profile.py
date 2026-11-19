"""`/show-profile` (agent-callable, headless allowed): the profile, sources and filter file (R17).

It reads only `profile/profile.md`, `profile/checklist.json` (reconciled, never
written, Phase 3 R14), `profile/sources/manifest.json` and `listing_filter_file.md`
(shown without its internal `profile-sha256` marker); never a source's `text.md` or
anything in `quarantine/`.
"""

from __future__ import annotations

from app.intake.checklist import ITEMS
from app.intake.filter_file import filter_path, strip_profile_hash
from app.intake.profile import (
    ProfileError,
    is_filled,
    item_body,
    item_title,
    load_profile,
    profile_path,
)
from app.intake.sources import SourcesError, load_manifest, manifest_path, sources_dir
from app.intake.status import checklist_path, read_status
from app.messages import short_error
from app.schemas.profile import ProfileItemView, ShowProfileInput, ShowProfileOutput, SourceView
from app.schemas.sources import Manifest
from app.tools.context import ToolContext
from app.tools.registry import Tool


def _read_filter(ctx: ToolContext) -> str | None:
    path = filter_path(ctx.workspace)
    try:
        return strip_profile_hash(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        raise ProfileError(
            "profile.filter_unreadable", path=str(path), detail=short_error(exc)
        ) from exc


def handle(ctx: ToolContext, inputs: ShowProfileInput) -> ShowProfileOutput:
    doc = load_profile(ctx.workspace)
    manifest_error = None
    try:
        manifest = load_manifest(ctx.workspace)
    except SourcesError as exc:
        manifest, manifest_error = Manifest(), exc.text(ctx.texts)
    status, invalid = read_status(ctx.workspace, doc, manifest)
    status_note = (
        ctx.msg("intake.note.status_rebuilt", path=str(checklist_path(ctx.workspace)))
        if invalid
        else None
    )
    views = []
    for item in ITEMS:
        item_status = status.items[item.id]
        views.append(
            ProfileItemView(
                id=item.id,
                group=item.group,
                title=item_title(doc, item.id, ctx.texts),
                text=item_body(doc, item.id, manifest),
                missing=not is_filled(doc, item.id, manifest),
                state=item_status.state,
                note=item_status.note,
                origins=item_status.origins,
            )
        )
    root = sources_dir(ctx.workspace)
    sources = [
        SourceView(slug=e.slug, origin=e.origin, type=e.type, cv=e.cv, path=str(root / e.slug))
        for e in manifest.sources
    ]
    return ShowProfileOutput(
        items=views,
        sources=sources,
        filter_text=_read_filter(ctx),
        manifest_error=manifest_error,
        status_note=status_note,
        profile_path=str(profile_path(ctx.workspace)),
        checklist_path=str(checklist_path(ctx.workspace)),
        sources_dir=str(root),
        manifest_path=str(manifest_path(ctx.workspace)),
        filter_path=str(filter_path(ctx.workspace)),
    )


TOOL = Tool(
    name="show-profile",
    input_model=ShowProfileInput,
    output_model=ShowProfileOutput,
    handler=handle,
    agent_callable=True,
)
