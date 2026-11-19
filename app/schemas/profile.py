"""The profile model (`profile.md`, R13, T7), the merge result and `-show-profile` (R17)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

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
    """Text for one item extracted from a source or the introduction (fill-only, R13).

    `relation` is set by the extraction step when the item was already filled; `origin`
    names where the text came from (a source slug or origin, or the introduction).
    """

    model_config = ConfigDict(extra="forbid")

    item_id: ItemId
    text: str
    relation: Relation | None = None
    origin: str = ""


class Conflict(BaseModel):
    """New text that contradicts a filled item; the item was left as is (R13)."""

    model_config = ConfigDict(extra="forbid")

    item_id: ItemId
    current: str
    proposed: str
    origin: str = ""


class MergeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    changed: list[ItemId] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)


# --- `-show-profile` -----------------------------------------------------------------


class ShowProfileInput(ToolInput):
    pass


class ProfileItemView(BaseModel):
    id: ItemId
    group: Group
    title: str
    text: str
    missing: bool


class SourceView(BaseModel):
    slug: str
    origin: str
    type: SourceType
    cv: bool
    path: str


class ShowProfileOutput(ToolOutput):
    """What `-show-profile` read from `profile.md`, `manifest.json` and the filter file.

    `filter_text` is `None` while `listing_filter_file.md` does not exist;
    `manifest_error` is the localized reason when `manifest.json` cannot be read.
    """

    items: list[ProfileItemView]
    sources: list[SourceView] = Field(default_factory=list)
    filter_text: str | None = None
    manifest_error: str | None = None
    profile_path: str
    sources_dir: str
    manifest_path: str
    filter_path: str

    def render(self, ctx: ToolContext) -> None:
        from rich.text import Text

        from app.guard.quarantine import escape_controls

        def say(text: str, style: str | None = None) -> None:
            ctx.console.print(Text(escape_controls(text), style=style or ""), highlight=False)

        for group in ("must", "nice"):
            say(ctx.msg(f"profile.group.{group}"), "bold")
            for view in (v for v in self.items if v.group == group):
                if view.missing:
                    say(f"## {view.title}  ({ctx.msg('profile.show.missing')})", "yellow")
                    continue
                say(f"## {view.title}", "cyan")
                say(view.text)
            say("")
        say(ctx.msg("profile.show.sources"), "bold")
        if self.manifest_error:
            say(self.manifest_error, "yellow")
        elif not self.sources:
            say(ctx.msg("profile.show.no_sources"))
        for source in self.sources:
            flag = f"  [{ctx.msg('profile.show.cv_flag')}]" if source.cv else ""
            say(f"- {source.origin} ({source.type}){flag} → {source.path}")
        say("")
        say(ctx.msg("profile.show.filter"), "bold")
        if self.filter_text is None:
            say(ctx.msg("profile.show.no_filter"))
        else:
            say(self.filter_text.rstrip())
        say("")
        say(ctx.msg("profile.show.files"), "bold")
        for key, path in (
            ("profile", self.profile_path),
            ("sources", self.sources_dir),
            ("manifest", self.manifest_path),
            ("filter", self.filter_path),
        ):
            say(f"- {ctx.msg(f'profile.show.file.{key}')}: {path}")
