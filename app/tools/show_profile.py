"""`-show-profile` (agent-callable, headless allowed): the profile, sources and filter file (R17).

It reads only `profile/profile.md`, `profile/sources/manifest.json` and
`listing_filter_file.md`; never a source's `text.md` or anything in `quarantine/`.
"""

from __future__ import annotations

from app.intake.checklist import CV_ITEM, ITEMS
from app.intake.profile import (
    ProfileError,
    cv_body,
    filter_path,
    is_filled,
    load_profile,
    profile_path,
)
from app.intake.sources import SourcesError, load_manifest, manifest_path, sources_dir
from app.messages import short_error
from app.schemas.profile import ProfileItemView, ShowProfileInput, ShowProfileOutput, SourceView
from app.schemas.sources import Manifest
from app.tools.context import ToolContext
from app.tools.registry import Tool


def _read_filter(ctx: ToolContext) -> str | None:
    path = filter_path(ctx.workspace)
    try:
        return path.read_text(encoding="utf-8")
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
    views = []
    for item in ITEMS:
        section = doc.items.get(item.id)  # type: ignore[call-overload]
        text = cv_body(manifest) if item.id == CV_ITEM else doc.body(item.id)
        views.append(
            ProfileItemView(
                id=item.id,  # type: ignore[arg-type]
                group=item.group,
                title=(section.title if section else "") or item.title(ctx.texts),
                text=text,
                missing=not is_filled(doc, item.id, manifest),
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
        profile_path=str(profile_path(ctx.workspace)),
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
