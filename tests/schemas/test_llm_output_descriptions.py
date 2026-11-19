"""Every field of an LLM output schema tells the model what it means (T8, T9)."""

from __future__ import annotations

import pytest

from app.schemas.filter import FilterFile
from app.schemas.intake import ExtractedValue, Extraction


@pytest.mark.parametrize("schema", [Extraction, ExtractedValue, FilterFile])
def test_every_field_has_a_description(schema):
    for name, field in schema.model_fields.items():
        assert field.description and field.description.strip(), f"{schema.__name__}.{name}"


def test_descriptions_reach_the_json_schema():
    props = Extraction.model_json_schema()["properties"]
    assert all(p.get("description") for p in props.values())


def test_relation_explains_each_value():
    text = ExtractedValue.model_fields["relation"].description
    for word in ("match", "adds", "contradicts"):
        assert word in text
