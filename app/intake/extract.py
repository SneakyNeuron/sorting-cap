"""Extraction: a source text into checklist items (T8), by the toolless source reader.

One structured-output call on the chat model from settings, into `Extraction`. The
system prompt is the `intake/extract` prompt (read through `app/texts.py`); the user
message is JSON with the current items and the new text, so the text stays data.
Texts longer than `CHAR_BUDGET` characters are cut and the cut reported.

The call goes through `app.providers.structured.invoke_structured` (the provider's
structured-output method, invalid output retried with the error fed back, transient
errors retried). Any model failure left after that (an exception, invalid structured
output, no output) is returned as `ExtractOutcome.error`; the caller leaves the source
unextracted, so the next round reads it again. The extraction model gets
no tools.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from app.intake.checklist import CV_ITEM, ITEMS
from app.messages import short_error
from app.providers.structured import invoke_structured
from app.schemas.intake import Extraction, ExtractOutcome
from app.schemas.profile import ItemUpdate, ProfileDoc
from app.texts import Texts

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

CHAR_BUDGET = 12_000


def cut(text: str, budget: int = CHAR_BUDGET) -> tuple[str, bool]:
    """The text within the budget, and whether it was cut."""
    return (text, False) if len(text) <= budget else (text[:budget], True)


def build_input(texts: Texts, doc: ProfileDoc, *, text: str, source: bool) -> str:
    """The user message: JSON with the items and the text (no prose)."""
    items = [
        {"id": i.id, "title": i.title(texts), "current": doc.body(i.id).strip()}
        for i in ITEMS
        if i.id != CV_ITEM
    ]
    payload = {"items": items, "is_source": source, "text": text}
    return json.dumps(payload, ensure_ascii=False, indent=1)


def extract(
    model: BaseChatModel,
    texts: Texts,
    doc: ProfileDoc,
    *,
    text: str,
    source: bool = False,
) -> ExtractOutcome:
    """Run one extraction; never raises for a model failure (see the module doc)."""
    from langchain_core.messages import HumanMessage, SystemMessage

    body, truncated = cut(text)
    messages = [
        SystemMessage(texts.prompt("intake", "extract")),
        HumanMessage(build_input(texts, doc, text=body, source=source)),
    ]
    try:
        result = invoke_structured(model, Extraction, messages, texts)
    except Exception as exc:  # providers, parsers and validation raise their own types
        return ExtractOutcome(truncated=truncated, error=short_error(exc))
    return ExtractOutcome(extraction=result, truncated=truncated)


def to_updates(
    extraction: Extraction,
    origin: str,
    *,
    source: str | None = None,
) -> list[ItemUpdate]:
    """Fill-only updates for every extracted item.

    `source` is the checklist origin of the source (`file:<slug>` / `web:<slug>`),
    `None` for text from the conversation; each update carries the reader's `gap`.
    """
    return [
        ItemUpdate(
            item_id=name,
            text=value.text.strip(),
            relation=value.relation,
            origin=origin,
            gap=(value.gap or "").strip() or None,
            source=source,
        )
        for name, value in extraction.values()
    ]
