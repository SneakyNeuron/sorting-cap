"""`profile/checklist.json`: the stored state, hole note and origins per checklist item
(Phase 3 R3, R4, T2).

`profile.md` stays the source of truth for content; this file only adds the state
(`missing`, `partial`, `known`), the hole note and where each item was learned from.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from app.schemas.profile import ItemId, ItemState

# The states a tool or the source reader may set; `missing` follows from an empty section.
SetState = Literal["partial", "known"]

# Fixed origins; a source origin is `file:<slug>` or `web:<slug>`.
ORIGIN_CONVERSATION = "conversation"
ORIGIN_HAND_EDIT = "hand_edit"
ORIGIN_PROFILE = "profile"
ORIGIN_PATTERN = r"^(conversation|hand_edit|profile|(file|web):[a-z0-9][a-z0-9-]*)$"


def dedupe(origins: list[str]) -> list[str]:
    """`origins` without repeats, in first-seen order."""
    return list(dict.fromkeys(origins))


class ItemStatus(BaseModel):
    """One item's stored status. `origins` are deduplicated in first-seen order."""

    model_config = ConfigDict(extra="forbid")

    state: ItemState
    note: str | None = None
    origins: list[str] = Field(default_factory=list)
    updated_at: AwareDatetime

    @field_validator("origins")
    @classmethod
    def _origins(cls, value: list[str]) -> list[str]:
        for origin in value:
            if not re.fullmatch(ORIGIN_PATTERN, origin):
                raise ValueError(f"unknown origin: {origin!r}")
        return dedupe(value)


class ChecklistStatus(BaseModel):
    """The whole `checklist.json`: `{"version": 1, "items": {<item id>: ItemStatus}}`."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    items: dict[ItemId, ItemStatus] = Field(default_factory=dict)


class StatusChange(BaseModel):
    """What one round learned about an item: its new state, hole note and origin.

    Built in code from a source value the reader filled (`partial` with the reader's
    `gap`, else `known`), a direct answer (`known`, note cleared), a CV flag, or a
    `mark_status` call (no `origin`: the item's origins are kept as they are).
    """

    model_config = ConfigDict(extra="forbid")

    item_id: ItemId
    state: SetState
    note: str | None = None
    origin: str | None = Field(default=None, pattern=ORIGIN_PATTERN)


class ItemHole(BaseModel):
    """A partial item and what is still missing, for the round summary (R12)."""

    model_config = ConfigDict(extra="forbid")

    item_id: ItemId
    note: str | None = None
