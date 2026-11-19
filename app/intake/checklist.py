"""The profile checklist (R12): item ids, order, must/nice grouping, titles and questions.

Titles and the prewritten questions are message keys (`profile.item.<id>.title`,
`profile.item.<id>.question`) in the cs and en catalogs; the LLM never writes a
question. `ITEMS` is in R12 order: every must-know item, then every nice-to-have item.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, TypeGuard, get_args

from app.schemas.profile import Group, ItemId
from app.texts import Texts

ITEM_IDS: tuple[ItemId, ...] = get_args(ItemId)
MUST_KNOW: tuple[ItemId, ...] = ("target_roles", "experience", "skills", "education", "languages")
CV_ITEM: Final = "cv"


@dataclass(frozen=True)
class Item:
    id: ItemId
    group: Group

    @property
    def title_key(self) -> str:
        return f"profile.item.{self.id}.title"

    @property
    def question_key(self) -> str:
        return f"profile.item.{self.id}.question"

    def title(self, texts: Texts) -> str:
        return texts.message(self.title_key)

    def question(self, texts: Texts) -> str:
        return texts.message(self.question_key)


ITEMS: tuple[Item, ...] = tuple(
    Item(item_id, "must" if item_id in MUST_KNOW else "nice") for item_id in ITEM_IDS
)
_BY_ID: dict[str, Item] = {i.id: i for i in ITEMS}


def is_item_id(value: str) -> TypeGuard[ItemId]:
    return value in _BY_ID


def item(item_id: str) -> Item:
    """The checklist item with this id; `KeyError` for an unknown id."""
    return _BY_ID[item_id]


def items_in_group(group: Group) -> list[Item]:
    return [i for i in ITEMS if i.group == group]


def message_keys() -> list[str]:
    """Every catalog key the checklist needs (titles and questions of all items)."""
    return [key for i in ITEMS for key in (i.title_key, i.question_key)]
