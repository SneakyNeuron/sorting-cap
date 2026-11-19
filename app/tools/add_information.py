"""`-add-information` (not agent-callable): one intake round and its summary (R9, R15, R16).

In chat without `--source` it runs the dialog graph (sources, introduction,
questions). With `--source` (repeatable), and always in the headless runner, it runs
the graph without the dialog: the given sources, dropped-in files and sources not yet
extracted are ingested, the profile filled and the filter file regenerated.

The provider and model are checked, and the model built, before anything touches the
workspace, so a missing model leaves it unchanged (R16). Guarded sources refused for
a missing classifier mark the output `incomplete` (exit 1 headless), as do a
failed filter generation and a source given with `--source` that failed (`error`;
failed drop-ins are only reported). A corrupt manifest, an unreadable profile and workspace
write failures end the round with a localized error (R10).
"""

from __future__ import annotations

from app.errors import LocalizedError, UserFacingError
from app.guard.classifier import is_headless
from app.intake.graph import run_round
from app.intake.profile import filter_path, profile_path
from app.intake.sources import SourcesError, load_manifest, manifest_path, sources_dir
from app.messages import short_error
from app.schemas.intake import AddInformationInput, AddInformationOutput, RoundResult
from app.schemas.sources import SourceResult
from app.tools.context import ToolContext, ToolError
from app.tools.registry import Tool


def _model(ctx: ToolContext):
    settings = ctx.settings
    if not settings.provider or not settings.model:
        raise ToolError(ctx.msg("add_information.no_model"))
    try:
        return ctx.model_factory(settings, ctx.credentials)
    except UserFacingError:
        raise
    except Exception as exc:  # providers raise their own types
        raise ToolError(
            ctx.msg(
                "add_information.model_unavailable",
                provider=settings.provider,
                detail=short_error(exc),
            )
        ) from exc


def in_summary(result: SourceResult) -> bool:
    """A drop-in already in the manifest stays in place and is not reported (R11)."""
    return not (result.status == "skipped_duplicate" and result.dropped_in)


def _source_dirs(ctx: ToolContext) -> list[str]:
    try:
        manifest = load_manifest(ctx.workspace)
    except SourcesError:
        return []
    root = sources_dir(ctx.workspace)
    return [str(root / e.slug) for e in manifest.sources]


def summary(
    ctx: ToolContext, result: RoundResult, *, explicit: bool = False
) -> AddInformationOutput:
    """The round's summary. `explicit`: the sources were given with `--source`, so a
    failed one (`error`, not a drop-in) also marks the run incomplete."""
    refused = any(s.status == "refused_no_classifier" for s in result.sources)
    filter_failed = any(n.kind == "model_error_filter" for n in result.notes)
    failed = explicit and any(s.status == "error" and not s.dropped_in for s in result.sources)
    return AddInformationOutput(
        incomplete=refused or filter_failed or failed,
        cancelled=result.cancelled,
        sources=[s for s in result.sources if in_summary(s)],
        changed=result.changed,
        missing=result.missing,
        conflicts=result.conflicts,
        notes=result.notes,
        profile_written=result.profile_written,
        filter_written=result.filter_written,
        profile_path=str(profile_path(ctx.workspace)),
        sources_dir=str(sources_dir(ctx.workspace)),
        source_dirs=_source_dirs(ctx),
        manifest_path=str(manifest_path(ctx.workspace)),
        filter_path=str(filter_path(ctx.workspace)),
    )


def handle(ctx: ToolContext, inputs: AddInformationInput) -> AddInformationOutput:
    model = _model(ctx)
    sources = [s for s in inputs.source if s.strip()]
    headless = bool(sources) or is_headless(ctx)
    built, ctx.model_factory = ctx.model_factory, lambda _settings, _credentials: model
    try:
        result = run_round(ctx, sources=sources, headless=headless)
    except LocalizedError as exc:  # SourcesError (corrupt manifest), ProfileError
        raise ToolError(exc.text(ctx.texts)) from exc
    except OSError as exc:  # a failed write to the workspace ends the round (R10)
        raise ToolError(ctx.msg("add_information.write_failed", detail=short_error(exc))) from exc
    finally:
        ctx.model_factory = built
    return summary(ctx, result, explicit=bool(sources))


TOOL = Tool(
    name="add-information",
    input_model=AddInformationInput,
    output_model=AddInformationOutput,
    handler=handle,
)
