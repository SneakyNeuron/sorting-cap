"""Intake: the extraction output (T8), the round's graph state and result, and
`/add-information` (R9, R15, R16, T10)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, TypedDict, get_args

from pydantic import BaseModel, ConfigDict, Field

from app.messages import say
from app.schemas.base import ToolInput, ToolOutput
from app.schemas.checklist import ItemHole
from app.schemas.profile import Conflict, ItemId, ProfilePaths, Relation
from app.schemas.sources import SourceResult

if TYPE_CHECKING:
    from app.tools.context import ToolContext

# --- extraction (T8) ---------------------------------------------------------------


class ExtractedValue(BaseModel):
    """What the text says about one checklist item.

    `relation` is set only when the item is already filled: `match` (nothing new),
    `adds` (new details, appended) or `contradicts` (a conflict for the user). `gap`
    is the hole note: what the item still lacks after this text (Phase 3 T8).
    """

    text: str = Field(
        description=(
            "What the text states about this item, as short Markdown in the user's words "
            "and language. Only facts the text actually gives count as a value; never "
            "guess or fill in from general knowledge."
        )
    )
    relation: Relation | None = Field(
        default=None,
        description=(
            "Set only when the item already has a current value, otherwise null. "
            "'match': the text says the same as the current value (nothing new); "
            "'adds': the text gives new details that fit with the current value and "
            "can be appended to it; 'contradicts': the text disagrees with the current "
            "value (a conflict the user resolves)."
        ),
    )
    gap: str | None = Field(
        default=None,
        description=(
            "What is still missing for this item after this text, as a short note in the "
            "text's language (for example 'no currency' for a salary without one, or "
            "'no levels' for languages without levels). Null when the value looks complete."
        ),
    )


def _item(meaning: str) -> Any:
    return Field(
        default=None,
        description=(
            f"{meaning} Null when the text says nothing about it; a question or a vague "
            "remark is not a value."
        ),
    )


class Extraction(BaseModel):
    """The structured output of one extraction call: a value per item the text covers.

    The CV item is not extracted (it is rendered from the manifest); `is_cv` says
    whether a source text is a CV or résumé.
    """

    target_roles: ExtractedValue | None = _item(
        "Target roles: the job titles or kinds of role the user is looking for."
    )
    experience: ExtractedValue | None = _item(
        "Work experience: past and current roles, employers, years and what the user did."
    )
    skills: ExtractedValue | None = _item(
        "Key skills: technologies, tools, methods and abilities the user has."
    )
    education: ExtractedValue | None = _item(
        "Education: degrees, schools, fields of study, certificates and courses."
    )
    languages: ExtractedValue | None = _item(
        "Languages: the languages the user speaks and how well (level)."
    )
    salary: ExtractedValue | None = _item(
        "Salary range: the pay the user wants, with amount, currency and period if given."
    )
    contract_type: ExtractedValue | None = _item(
        "Contract type: the employment forms the user accepts, e.g. HPP (full employment), "
        "IČO (self-employed), DPP or DPČ (part-time agreements), full-time or part-time."
    )
    locations: ExtractedValue | None = _item(
        "Locations: the cities, regions or countries where the user wants to work."
    )
    work_mode: ExtractedValue | None = _item(
        "Work mode: on-site, hybrid or remote, and any limits such as office days."
    )
    start_date: ExtractedValue | None = _item(
        "Earliest start date: when the user can start, or their notice period."
    )
    industries: ExtractedValue | None = _item(
        "Industries and companies: sectors or employers the user prefers or avoids."
    )
    is_cv: bool = Field(
        default=False,
        description=(
            "True only when the text itself is the user's CV or résumé (a source, not "
            "a chat answer); false for other documents, notes and web pages."
        ),
    )

    def values(self) -> list[tuple[ItemId, ExtractedValue]]:
        """The non-empty values in checklist order."""
        names: tuple[ItemId, ...] = get_args(ItemId)
        out = []
        for name in names:
            value = getattr(self, name, None)  # the CV item has no field
            if isinstance(value, ExtractedValue) and value.text.strip():
                out.append((name, value))
        return out


class ExtractOutcome(BaseModel):
    """One extraction call: the output, or `error` (a short reason) on a model error."""

    model_config = ConfigDict(extra="forbid")

    extraction: Extraction | None = None
    truncated: bool = False
    error: str | None = None


# --- round state (T10) -------------------------------------------------------------


class RoundState(TypedDict, total=False):
    """The intake graph's state. Plain JSON values only, so the checkpointer stores
    them without custom types; models are dumped with `model_dump(mode="json")`.

    `base` is the profile as read at the start of the round (`ProfileDoc`); nothing is
    written before the `write` node. `updates` are fill-only `ItemUpdate`s from sources
    and the introduction, `answers` the direct answers (item id → text) that replace
    their items. `collecting` is true while sources are being asked for, `current` is
    the item of the last question asked. `extracted` are the slugs extracted this round
    (marked in the manifest after `profile.md` is written), `cv_slugs` the ones the
    extraction called a CV. `status` is the checklist status reconciled at the start
    (`ChecklistStatus`), `status_stored` the stored file as read (`None` when missing
    or invalid), so `write` saves the status only when it differs (Phase 3 R12).
    """

    headless: bool
    specs: list[str]
    base: dict[str, Any]
    profile_existed: bool
    sources: list[dict[str, Any]]
    updates: list[dict[str, Any]]
    answers: dict[str, str]
    asked: list[str]
    current: str | None
    collecting: bool
    extracted: list[str]
    cv_slugs: list[str]
    notes: list[dict[str, Any]]
    status: dict[str, Any]
    status_stored: dict[str, Any] | None
    done: bool
    result: dict[str, Any] | None


# --- round result (R15) ------------------------------------------------------------

NoteKind = Literal[
    "truncated",
    "model_error_answer",
    "model_error_intro",
    "model_error_source",
    "source_text_missing",
    "model_error_filter",
    "filter_interrupted",
    "filter_text_missing",
    "status_rebuilt",
]


# Notes saying the filter file was not regenerated although it was due (R14).
FILTER_NOT_WRITTEN: frozenset[str] = frozenset({"model_error_filter", "filter_interrupted"})


class RoundNote(BaseModel):
    """Something the summary must mention; `text` is already localized."""

    model_config = ConfigDict(extra="forbid")

    kind: NoteKind
    subject: str
    text: str


class RoundResult(BaseModel):
    """What one intake round did.

    `cancelled`: the round ended by Ctrl-C / Ctrl-D; `profile.md` was not written and
    only `sources` (already copied) is meaningful. `profile_changed`: an item or the CV
    section changed. `extracted`: slugs marked `extracted` in the manifest.
    `filter_written`: `listing_filter_file.md` was regenerated this round (R14).
    `partial`: items left partial, with their hole notes; `status_written`:
    `checklist.json` was written (Phase 3 R12).
    """

    model_config = ConfigDict(extra="forbid")

    cancelled: bool = False
    sources: list[SourceResult] = Field(default_factory=list)
    changed: list[ItemId] = Field(default_factory=list)
    missing: list[ItemId] = Field(default_factory=list)
    partial: list[ItemHole] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    notes: list[RoundNote] = Field(default_factory=list)
    extracted: list[str] = Field(default_factory=list)
    profile_written: bool = False
    status_written: bool = False
    profile_changed: bool = False
    filter_written: bool = False


# --- `/add-information` (R9, R15, R16) -------------------------------------------------


class AddInformationInput(ToolInput):
    """`--source` (repeatable): ingest these and the dropped-in files, ask no questions."""

    source: list[str] = Field(default_factory=list)


class AddInformationOutput(ToolOutput, ProfilePaths):
    """The round summary (R15).

    `sources` are this round's source outcomes, without duplicate drop-ins (R11).
    `source_dirs` is every stored source folder from the manifest.
    """

    cancelled: bool = False
    sources: list[SourceResult] = Field(default_factory=list)
    changed: list[ItemId] = Field(default_factory=list)
    missing: list[ItemId] = Field(default_factory=list)
    partial: list[ItemHole] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    notes: list[RoundNote] = Field(default_factory=list)
    profile_written: bool = False
    status_written: bool = False
    filter_written: bool = False
    source_dirs: list[str] = Field(default_factory=list)

    def render(self, ctx: ToolContext) -> None:
        from pathlib import Path

        from app.intake.checklist import item

        def titles(ids: list[ItemId]) -> str:
            return ", ".join(item(i).title(ctx.texts) for i in ids)

        def folder(slug: str | None) -> str:
            return str(Path(self.sources_dir) / slug) if slug else self.sources_dir

        if self.cancelled:
            say(ctx.console, ctx.msg("add_information.cancelled"), "yellow")
        else:
            say(ctx.console, ctx.msg("add_information.summary.title"), "bold")
            if self.changed:
                say(
                    ctx.console,
                    ctx.msg("add_information.summary.changed", items=titles(self.changed)),
                )
            else:
                say(ctx.console, ctx.msg("add_information.summary.unchanged"))
            if self.missing:
                say(
                    ctx.console,
                    ctx.msg("add_information.summary.missing", items=titles(self.missing)),
                )
            else:
                say(ctx.console, ctx.msg("add_information.summary.complete"))
            if self.partial:
                say(ctx.console, ctx.msg("add_information.summary.partial"))
                for hole in self.partial:
                    title = item(hole.item_id).title(ctx.texts)
                    gap = " ".join((hole.note or "").split())
                    say(ctx.console, f"- {title}: {gap}" if gap else f"- {title}")
            if self.conflicts:
                say(ctx.console, ctx.msg("add_information.summary.conflicts"), "yellow")
                for c in self.conflicts:
                    line = ctx.msg(
                        "add_information.summary.conflict",
                        title=item(c.item_id).title(ctx.texts),
                        current=" ".join(c.current.split()),
                        proposed=" ".join(c.proposed.split()),
                        origin=c.origin,
                    )
                    say(ctx.console, f"- {line}", "yellow")
        say(ctx.console, "")
        say(ctx.console, ctx.msg("add_information.summary.sources"), "bold")
        if not self.sources:
            say(ctx.console, ctx.msg("add_information.summary.no_sources"))
        styles = {"added": None, "skipped_duplicate": "dim"}
        for s in self.sources:
            line = ctx.msg(
                f"add_information.source.{s.status}",
                origin=s.origin,
                path=folder(s.slug),
                id=s.quarantine_id or "",
                reason=s.reason or "",
            )
            say(ctx.console, f"- {line}", styles.get(s.status, "yellow"))
        if self.notes:
            say(ctx.console, "")
            say(ctx.console, ctx.msg("add_information.summary.notes"), "bold")
            for note in self.notes:
                say(ctx.console, f"- {note.text}", "yellow")
        filter_failed = any(n.kind in FILTER_NOT_WRITTEN for n in self.notes)
        if not self.cancelled and not filter_failed:  # a failure is explained in the notes
            say(ctx.console, "")
            key = "filter_written" if self.filter_written else "filter_kept"
            say(ctx.console, ctx.msg(f"add_information.summary.{key}"))
        say(ctx.console, "")
        say(ctx.console, ctx.msg("add_information.summary.files"), "bold")
        say(ctx.console, f"- {ctx.msg('add_information.file.profile')}: {self.profile_path}")
        say(
            ctx.console,
            f"- {ctx.msg('add_information.file.checklist')}: {self.checklist_path}",
        )
        for path in self.source_dirs:
            say(ctx.console, f"- {ctx.msg('add_information.file.source')}: {path}")
        say(ctx.console, f"- {ctx.msg('add_information.file.manifest')}: {self.manifest_path}")
        say(ctx.console, f"- {ctx.msg('add_information.file.filter')}: {self.filter_path}")
