"""The intake agent (Phase 3 T4, T5): the arguments and results of its five in-round
tools, the source report it sees instead of source text (R7), and the plain-JSON state
of one conversational round.

The tools are not registry tools: they exist only inside an `/add-information` round
and are bound to the intake agent alone. Every argument field carries a description,
as it reaches the model in the tool schema.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.checklist import ChecklistStatus, SetState
from app.schemas.intake import RoundNote
from app.schemas.profile import ItemId, ItemState, ItemUpdate, ProfileDoc
from app.schemas.sources import SourceErrorCode, SourceResult, SourceStatus

# --- tool arguments ----------------------------------------------------------------


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RecordItemArgs(_Args):
    """`record_item`: write what the user said about one checklist item (R8)."""

    item_id: ItemId = Field(
        description=(
            "The checklist item the user's words are about, by its id from the status "
            "message. Not 'cv': a CV is a file, given with ingest_source(cv=true)."
        )
    )
    text: str = Field(
        description=(
            "The value for the item as short Markdown, in the user's words and language; "
            "only what the user said, nothing guessed."
        )
    )
    mode: Literal["replace", "append"] = Field(
        default="replace",
        description=(
            "'replace': the text becomes the item's whole value (the user corrected or "
            "restated it); 'append': the text is added as a new paragraph after the "
            "current value (the user added details)."
        ),
    )


class MarkStatusArgs(_Args):
    """`mark_status`: set a filled item's state and hole note (R9)."""

    item_id: ItemId = Field(description="The checklist item, by its id from the status message.")
    state: SetState = Field(
        description=(
            "'partial': the item has a value but something is still missing (say what in "
            "note); 'known': the item is complete."
        )
    )
    note: str | None = Field(
        default=None,
        description=(
            "For 'partial': a short note on what is still missing, in the user's language "
            "(for example 'no currency'). Ignored for 'known'."
        ),
    )


class IngestSourceArgs(_Args):
    """`ingest_source`: add a file or web page the user named (R6)."""

    spec: str = Field(
        description=(
            "The file path or URL as the user wrote it this round, or a path find_files "
            "returned. Give web pages as full URLs with https:// (for example "
            "https://firma.cz/kariera)."
        )
    )
    cv: bool = Field(
        default=False,
        description="True when the user said this file is their CV or résumé.",
    )


class FindFilesArgs(_Args):
    """`find_files`: search the user's folders for a file by name (R18)."""

    name: str = Field(
        description=(
            "Part of the file name to look for (case and diacritics are ignored), for "
            "example 'cv' or 'zivotopis'."
        )
    )
    folder: str | None = Field(
        default=None,
        description=(
            "The folder the user named (for example '~/Downloads'), or null to search the "
            "current folder, Downloads, Documents, Desktop and the home folder."
        ),
    )


class FinishArgs(_Args):
    """`finish`: end the round; the profile is saved (R2)."""


# --- tool results (what the agent receives) ----------------------------------------

RecordRefusal = Literal["cv_needs_source", "empty_text"]
MarkRefusal = Literal["item_empty"]
IngestRefusal = Literal[
    "empty", "url_not_typed", "path_not_typed", "local_host", "invalid_url", "declined"
]


class SourceReport(BaseModel):
    """What the agent learns about one source instead of its text (R7): its origin,
    status, error code, the items it filled, added to or conflicted with, and whether it
    counts as the CV, with the resulting state of each item it touched. A quarantined
    source is reported by status only."""

    model_config = ConfigDict(extra="forbid")

    origin: str
    status: SourceStatus
    error: SourceErrorCode | None = None
    read: bool = False
    filled: list[ItemId] = Field(default_factory=list)
    added_to: list[ItemId] = Field(default_factory=list)
    conflicts: list[ItemId] = Field(default_factory=list)
    cv: bool = False
    states: dict[ItemId, ItemState] = Field(default_factory=dict)


class RecordItemResult(BaseModel):
    ok: bool
    item_id: ItemId
    refused: RecordRefusal | None = None


class MarkStatusResult(BaseModel):
    ok: bool
    item_id: ItemId
    state: SetState | None = None
    refused: MarkRefusal | None = None


class IngestSourceResult(BaseModel):
    ok: bool
    spec: str
    refused: IngestRefusal | None = None
    report: SourceReport | None = None


class FindFilesResult(BaseModel):
    """Found paths (controls escaped), newest first, or an error code."""

    paths: list[str] = Field(default_factory=list)
    error: str | None = None
    truncated: bool = False


class FinishResult(BaseModel):
    ok: bool = True


# --- round state -------------------------------------------------------------------

EventKind = Literal["update", "answer", "mark", "cv"]


class RoundEvent(BaseModel):
    """One thing the round learned, in order, for the checklist status: a source value
    (`update`, `index` into `RoundData.updates`), a direct answer, a `mark_status`, or
    a source flagged as the CV (`origin` its `file:`/`web:` origin)."""

    model_config = ConfigDict(extra="forbid")

    kind: EventKind
    item_id: ItemId
    index: int | None = None
    state: SetState | None = None
    note: str | None = None
    origin: str | None = None


class RoundData(BaseModel):
    """One conversational round's state, plain JSON (Phase 3 T3, T5).

    `base` is `profile.md` as read at the start and `started` the reconciled status
    then; nothing is written before the round ends. `answers` are direct answers
    (item id → value, replacing the item); `updates` fill-only source values;
    `events` the order in which the status learned things. `user_messages` and
    `found` feed the `ingest_source` allow rule (T7). `reports` are the source reports
    not yet shown to the agent (the next context message carries them).
    """

    model_config = ConfigDict(extra="forbid")

    base: ProfileDoc = Field(default_factory=ProfileDoc)
    started: ChecklistStatus = Field(default_factory=ChecklistStatus)
    user_messages: list[str] = Field(default_factory=list)
    found: list[str] = Field(default_factory=list)
    answers: dict[ItemId, str] = Field(default_factory=dict)
    updates: list[ItemUpdate] = Field(default_factory=list)
    events: list[RoundEvent] = Field(default_factory=list)
    sources: list[SourceResult] = Field(default_factory=list)
    extracted: list[str] = Field(default_factory=list)
    cv_slugs: list[str] = Field(default_factory=list)
    notes: list[RoundNote] = Field(default_factory=list)
    reports: list[SourceReport] = Field(default_factory=list)
    finished: bool = False
