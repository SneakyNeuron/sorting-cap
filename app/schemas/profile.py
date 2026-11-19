"""The profile model (`profile.md`, R13, T7), the merge result and `/show-profile` (R17)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.messages import say
from app.schemas.base import ToolInput, ToolOutput
from app.schemas.sources import SourceType

if TYPE_CHECKING:
    from app.tools.context import ToolContext

# The stable ids used in the `<!-- item:<id> -->` heading comments of `profile.md` (T7),
# in R12 order; `app/intake/checklist.py` builds the checklist from them.
ItemId = Literal[
    "target_roles",
    "experience",
    "skills",
    "education",
    "languages",
    "cv",
    "salary",
    "contract_type",
    "locations",
    "work_mode",
    "start_date",
    "industries",
]
Group = Literal["must", "nice"]
# A checklist item's stored state (Phase 3 R3, `profile/checklist.json`).
ItemState = Literal["missing", "partial", "known"]

# How new text for an already filled item relates to it (T8): ignored, appended or a conflict.
Relation = Literal["match", "adds", "contradicts"]


class ItemSection(BaseModel):
    """One known section: its heading title as in the file and its Markdown body (stripped)."""

    model_config = ConfigDict(extra="forbid")

    title: str
    body: str = ""


class UnknownSection(BaseModel):
    """A `## ` section without a known id comment, kept verbatim.

    `after` is the item id of the known section it followed in the file (`None`: it
    came before every known section), so it is written back in the same place.
    """

    model_config = ConfigDict(extra="forbid")

    after: ItemId | None = None
    raw: str


class ProfileDoc(BaseModel):
    """`profile.md` parsed: the header text, the known sections by id, unknown sections.

    An item absent from `items` is rendered with its localized title and an empty body.
    """

    model_config = ConfigDict(extra="forbid")

    header: str = ""
    items: dict[ItemId, ItemSection] = Field(default_factory=dict)
    unknown: list[UnknownSection] = Field(default_factory=list)

    def body(self, item_id: ItemId) -> str:
        section = self.items.get(item_id)
        return section.body if section else ""


class ItemUpdate(BaseModel):
    """Text for one item extracted from a source (fill-only, R13).

    `relation` is set by the extraction step when the item was already filled; `origin`
    names the source the text came from (its slug or origin).
    `gap` is the reader's hole note for the value; `source` the checklist origin of the
    source (`file:<slug>` / `web:<slug>`), `None` for text from the conversation.
    """

    model_config = ConfigDict(extra="forbid")

    item_id: ItemId
    text: str
    relation: Relation | None = None
    origin: str = ""
    gap: str | None = None
    source: str | None = None


class Conflict(BaseModel):
    """New text that contradicts a filled item; the item was left as is (R13)."""

    model_config = ConfigDict(extra="forbid")

    item_id: ItemId
    current: str
    proposed: str
    origin: str = ""


class MergeResult(BaseModel):
    """`changed`: items whose body changed; `applied`: indexes of the updates that were
    written (filled an empty item or appended to it)."""

    model_config = ConfigDict(extra="forbid")

    changed: list[ItemId] = Field(default_factory=list)
    applied: list[int] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)


# --- `/show-profile` -----------------------------------------------------------------


class ShowProfileInput(ToolInput):
    pass


class ProfileItemView(BaseModel):
    """One item as shown: its text and its reconciled checklist status (Phase 3 R14)."""

    id: ItemId
    group: Group
    title: str
    text: str
    missing: bool
    state: ItemState = "missing"
    note: str | None = None
    origins: list[str] = Field(default_factory=list)


class SourceView(BaseModel):
    slug: str
    origin: str
    type: SourceType
    cv: bool
    path: str


class ProfilePaths(BaseModel):
    """The workspace paths shown by `/show-profile` and the `/add-information` summary."""

    profile_path: str
    checklist_path: str
    sources_dir: str
    manifest_path: str
    filter_path: str


class ShowProfileOutput(ToolOutput, ProfilePaths):
    """What `/show-profile` read from `profile.md`, `manifest.json` and the filter file.

    `filter_text` is `None` while `listing_filter_file.md` does not exist;
    `manifest_error` is the localized reason when `manifest.json` cannot be read;
    `status_note` the localized note when `checklist.json` could not be read and the
    status shown was rebuilt (it is never written here).
    """

    items: list[ProfileItemView]
    sources: list[SourceView] = Field(default_factory=list)
    filter_text: str | None = None
    manifest_error: str | None = None
    status_note: str | None = None

    def render(self, ctx: ToolContext) -> None:
        if self.status_note:
            say(ctx.console, self.status_note, "yellow")
            say(ctx.console, "")
        for group in ("must", "nice"):
            say(ctx.console, ctx.msg(f"profile.group.{group}"), "bold")
            for view in (v for v in self.items if v.group == group):
                if view.missing:
                    say(
                        ctx.console,
                        f"## {view.title}  ({ctx.msg('profile.show.missing')})",
                        "yellow",
                    )
                    continue
                if view.state == "partial":
                    hole = ctx.msg("profile.show.partial")
                    note = " ".join((view.note or "").split())
                    label = f"{hole}: {note}" if note else hole
                    say(ctx.console, f"## {view.title}  ({label})", "yellow")
                else:
                    say(ctx.console, f"## {view.title}", "cyan")
                if view.origins:
                    origins = ", ".join(_origin_label(ctx, o) for o in view.origins)
                    say(ctx.console, ctx.msg("profile.show.origins", origins=origins), "dim")
                say(ctx.console, view.text)
            say(ctx.console, "")
        say(ctx.console, ctx.msg("profile.show.sources"), "bold")
        if self.manifest_error:
            say(ctx.console, self.manifest_error, "yellow")
        elif not self.sources:
            say(ctx.console, ctx.msg("profile.show.no_sources"))
        for source in self.sources:
            flag = f"  [{ctx.msg('profile.show.cv_flag')}]" if source.cv else ""
            say(ctx.console, f"- {source.origin} ({source.type}){flag} → {source.path}")
        say(ctx.console, "")
        say(ctx.console, ctx.msg("profile.show.filter"), "bold")
        if self.filter_text is None:
            say(ctx.console, ctx.msg("profile.show.no_filter"))
        else:
            say(ctx.console, self.filter_text.rstrip())
        say(ctx.console, "")
        say(ctx.console, ctx.msg("profile.show.files"), "bold")
        for key, path in (
            ("profile", self.profile_path),
            ("checklist", self.checklist_path),
            ("sources", self.sources_dir),
            ("manifest", self.manifest_path),
            ("filter", self.filter_path),
        ):
            say(ctx.console, f"- {ctx.msg(f'profile.show.file.{key}')}: {path}")


def _origin_label(ctx: ToolContext, origin: str) -> str:
    """A fixed origin in the UI language; `file:<slug>` / `web:<slug>` as stored."""
    key = f"profile.origin.{origin}"
    return ctx.msg(key) if ":" not in origin and ctx.texts.has(key) else origin
