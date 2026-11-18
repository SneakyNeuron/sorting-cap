"""Intake: the extraction output (T8), the round's graph state and its result (R9, R15, T10)."""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field

from app.intake.checklist import ItemId
from app.schemas.profile import Conflict, Relation
from app.schemas.sources import SourceResult

# --- extraction (T8) ---------------------------------------------------------------


class ExtractedValue(BaseModel):
    """What the text says about one checklist item.

    `relation` is set only when the item is already filled: `match` (nothing new),
    `adds` (new details, appended) or `contradicts` (a conflict for the user).
    """

    text: str = Field(description="The item's value as short Markdown, in the user's words.")
    relation: Relation | None = Field(
        default=None,
        description="Only for items already filled: match, adds or contradicts.",
    )


class Extraction(BaseModel):
    """The structured output of one extraction call: a value per item the text covers.

    The CV item is not extracted (it is rendered from the manifest); `is_cv` says
    whether a source text is a CV or résumé.
    """

    target_roles: ExtractedValue | None = None
    experience: ExtractedValue | None = None
    skills: ExtractedValue | None = None
    education: ExtractedValue | None = None
    languages: ExtractedValue | None = None
    salary: ExtractedValue | None = None
    contract_type: ExtractedValue | None = None
    locations: ExtractedValue | None = None
    work_mode: ExtractedValue | None = None
    start_date: ExtractedValue | None = None
    industries: ExtractedValue | None = None
    is_cv: bool = Field(default=False, description="True when the text is a CV or résumé.")

    def values(self) -> list[tuple[str, ExtractedValue]]:
        """The non-empty values in checklist order."""
        out = []
        for name in type(self).model_fields:
            value = getattr(self, name)
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
    extraction called a CV.
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
    done: bool
    result: dict[str, Any] | None


# --- round result (R15) ------------------------------------------------------------

NoteKind = Literal[
    "truncated",
    "model_error_answer",
    "model_error_intro",
    "model_error_source",
    "source_text_missing",
]


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
    """

    model_config = ConfigDict(extra="forbid")

    cancelled: bool = False
    sources: list[SourceResult] = Field(default_factory=list)
    changed: list[ItemId] = Field(default_factory=list)
    missing: list[ItemId] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    notes: list[RoundNote] = Field(default_factory=list)
    extracted: list[str] = Field(default_factory=list)
    profile_written: bool = False
    profile_changed: bool = False
