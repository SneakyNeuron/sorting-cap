"""Extraction: text (an answer, the introduction or a source) into checklist items (T8).

One structured-output call on the chat model from settings, into `Extraction`. The
system prompt is the `intake/extract` prompt (read through `app/texts.py`); the user
message is JSON with the current items, the question being answered (if any) and the
new text, so the text stays data. Texts longer than `CHAR_BUDGET` characters are cut
and the cut reported.

Any model failure (an exception, invalid structured output, no output) is returned as
`ExtractOutcome.error`; the caller applies the fallbacks (answer kept verbatim, source
left unextracted). The extraction model gets no tools.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from app.intake.checklist import CV_ITEM, ITEMS, item
from app.messages import short_error
from app.schemas.intake import Extraction, ExtractOutcome
from app.schemas.profile import ItemUpdate, ProfileDoc
from app.texts import Texts

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

CHAR_BUDGET = 12_000


def cut(text: str, budget: int = CHAR_BUDGET) -> tuple[str, bool]:
    """The text within the budget, and whether it was cut."""
    return (text, False) if len(text) <= budget else (text[:budget], True)


def build_input(
    texts: Texts, doc: ProfileDoc, *, text: str, question: str | None, source: bool
) -> str:
    """The user message: JSON with the items, the question and the text (no prose)."""
    items = [
        {"id": i.id, "title": i.title(texts), "current": doc.body(i.id).strip()}
        for i in ITEMS
        if i.id != CV_ITEM
    ]
    asked = {"id": question, "question": item(question).question(texts)} if question else None
    payload = {"items": items, "question": asked, "is_source": source, "text": text}
    return json.dumps(payload, ensure_ascii=False, indent=1)


def extract(
    model: BaseChatModel,
    texts: Texts,
    doc: ProfileDoc,
    *,
    text: str,
    question: str | None = None,
    source: bool = False,
) -> ExtractOutcome:
    """Run one extraction; never raises for a model failure (see the module doc)."""
    from langchain_core.messages import HumanMessage, SystemMessage

    body, truncated = cut(text)
    messages = [
        SystemMessage(texts.prompt("intake", "extract")),
        HumanMessage(build_input(texts, doc, text=body, question=question, source=source)),
    ]
    try:
        result = model.with_structured_output(Extraction).invoke(messages)
        if isinstance(result, dict):
            result = Extraction.model_validate(result)
        if not isinstance(result, Extraction):
            raise ValueError("no structured output")
    except Exception as exc:  # providers, parsers and validation raise their own types
        return ExtractOutcome(truncated=truncated, error=short_error(exc))
    return ExtractOutcome(extraction=result, truncated=truncated)


def to_updates(extraction: Extraction, origin: str, *, skip: str | None = None) -> list[ItemUpdate]:
    """Fill-only updates for every extracted item except `skip` (the answered one)."""
    return [
        ItemUpdate(item_id=name, text=value.text.strip(), relation=value.relation, origin=origin)  # type: ignore[arg-type]
        for name, value in extraction.values()
        if name != skip
    ]
