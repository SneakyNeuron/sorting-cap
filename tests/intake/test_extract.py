"""Extraction step: structured output, input, character budget and model errors (T8)."""

from __future__ import annotations

import json

from helpers import structured_model
from langchain_core.exceptions import OutputParserException

from app.intake.extract import CHAR_BUDGET, cut, extract, to_updates
from app.schemas.intake import ExtractedValue, Extraction
from app.schemas.profile import ItemSection, ProfileDoc
from app.texts import Texts

EN = Texts("en")


def doc() -> ProfileDoc:
    return ProfileDoc(items={"skills": ItemSection(title="Key skills", body="Python")})


def test_returns_structured_output_and_sends_items_question_and_text():
    reply = Extraction(skills=ExtractedValue(text="Python, SQL", relation="adds"))
    model = structured_model(reply)
    outcome = extract(model, EN, doc(), text="I know SQL too", question="skills")
    assert outcome.extraction == reply and outcome.error is None and not outcome.truncated
    assert model.schemas == [Extraction]
    system, human = model.calls[0]
    assert system.content == EN.prompt("intake", "extract")
    payload = json.loads(human.content)
    assert payload["text"] == "I know SQL too"
    assert payload["question"] == {
        "id": "skills",
        "question": EN.message("profile.item.skills.question"),
    }
    assert payload["is_source"] is False
    by_id = {i["id"]: i for i in payload["items"]}
    assert by_id["skills"]["current"] == "Python"
    assert by_id["education"]["current"] == ""
    assert "cv" not in by_id


def test_dict_reply_is_validated():
    model = structured_model({"skills": {"text": "Go"}, "is_cv": True})
    outcome = extract(model, EN, ProfileDoc(), text="CV text", source=True)
    assert outcome.extraction.skills.text == "Go" and outcome.extraction.is_cv
    assert json.loads(model.calls[0][1].content)["is_source"] is True


def test_model_error_and_invalid_output_become_error():
    for failure in (RuntimeError("boom"), OutputParserException("bad json"), {"skills": 3}):
        replies = [failure] if isinstance(failure, RuntimeError) else [failure] * 3
        model = structured_model(*replies)
        outcome = extract(model, EN, ProfileDoc(), text="x")
        assert outcome.extraction is None and outcome.error
        assert model.queue == []


def test_uses_the_shared_structured_call_with_raw_output():
    model = structured_model(Extraction())
    extract(model, EN, ProfileDoc(), text="x")
    assert model.options == [{"method": None, "include_raw": True}]


def test_invalid_output_is_retried_with_the_error():
    model = structured_model({"skills": 3}, {"skills": {"text": "Go"}})
    outcome = extract(model, EN, ProfileDoc(), text="x")
    assert outcome.extraction.skills.text == "Go" and outcome.error is None
    assert len(model.calls) == 2 and len(model.calls[1]) == 4


def test_text_over_budget_is_cut_and_flagged():
    long = "a" * (CHAR_BUDGET + 500)
    model = structured_model(Extraction())
    outcome = extract(model, EN, ProfileDoc(), text=long, source=True)
    assert outcome.truncated
    assert json.loads(model.calls[0][1].content)["text"] == "a" * CHAR_BUDGET
    assert cut("short") == ("short", False)
    assert cut("a" * CHAR_BUDGET) == ("a" * CHAR_BUDGET, False)


def test_to_updates_skips_empty_and_answered_items():
    extraction = Extraction(
        skills=ExtractedValue(text=" Go "),
        education=ExtractedValue(text="  "),
        languages=ExtractedValue(text="English C1", relation="match"),
    )
    updates = to_updates(extraction, "cv.pdf", skip="languages")
    assert [(u.item_id, u.text, u.origin) for u in updates] == [("skills", "Go", "cv.pdf")]
