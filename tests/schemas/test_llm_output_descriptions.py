"""Every field of an LLM output schema tells the model what it means (T8, T9)."""

from __future__ import annotations

import ast
import importlib
from typing import get_args

import pytest
from pydantic import BaseModel

from app.paths import REPO_ROOT
from app.schemas.filter import FilterFile
from app.schemas.intake import ExtractedValue, Extraction

APP = REPO_ROOT / "app"


def _schema_args(tree: ast.AST) -> list[ast.expr]:
    """The schema argument of every `invoke_structured(model, schema, ...)` call."""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name != "invoke_structured":
            continue
        keyword = [k.value for k in node.keywords if k.arg == "schema"]
        found.append(keyword[0] if keyword else node.args[1])
    return found


def _nested_models(annotation: object) -> list[type[BaseModel]]:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation]
    return [m for arg in get_args(annotation) for m in _nested_models(arg)]


def _output_schemas() -> list[type[BaseModel]]:
    """Every schema passed to `invoke_structured` in `app/`, with the models nested in it.

    The schema argument must be a module-level name, so it can be resolved here.
    """
    schemas: list[type[BaseModel]] = []
    for path in sorted(APP.rglob("*.py")):
        rel = path.relative_to(REPO_ROOT).with_suffix("")
        args = _schema_args(ast.parse(path.read_text()))
        if not args:
            continue
        module = importlib.import_module(".".join(rel.parts))
        for arg in args:
            assert isinstance(arg, ast.Name), f"{rel}: schema must be a plain name"
            schemas.append(getattr(module, arg.id))
    seen: list[type[BaseModel]] = []
    while schemas:
        schema = schemas.pop()
        if schema in seen:
            continue
        seen.append(schema)
        for field in schema.model_fields.values():
            schemas.extend(_nested_models(field.annotation))
    return sorted(seen, key=lambda s: s.__name__)


OUTPUT_SCHEMAS = _output_schemas()


def test_collection_finds_known_schemas():
    assert {Extraction, ExtractedValue, FilterFile} <= set(OUTPUT_SCHEMAS)


def test_collection_reads_positional_and_keyword_schema():
    tree = ast.parse("invoke_structured(m, A, [], t)\nso.invoke_structured(m, schema=B)")
    assert [a.id for a in _schema_args(tree)] == ["A", "B"]


@pytest.mark.parametrize("schema", OUTPUT_SCHEMAS, ids=lambda s: s.__name__)
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
