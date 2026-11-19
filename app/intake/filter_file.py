"""`listing_filter_file.md`: generated from the profile and the source texts (R14, T9).

One structured-output call on the chat model from settings, into `FilterFile`; the
system prompt is the `filter/generate` prompt (read through `app/texts.py`), the user
message is JSON with `profile.md`, the stored source texts and the user's language,
so the texts stay data. The source texts together are cut at the same character
budget as extraction (T8); `profile.md` is sent whole. Code renders the Markdown with
the generated-file header and writes it with `write_private`, overwriting the old one.

It is regenerated only after a round that changed the profile, or when the file is
missing (`needs_filter`). Any model failure is returned as `FilterOutcome.error`; the
caller keeps the previous file and notes it. The generation model gets no tools.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from app.files import write_private
from app.intake.extract import CHAR_BUDGET
from app.intake.profile import filter_path
from app.messages import short_error
from app.schemas.filter import FilterFile, FilterOutcome
from app.texts import Texts

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

    from app.schemas.sources import Manifest

SECTIONS = ("summary", "must_match", "nice_to_match", "exclusions", "keywords_cs", "keywords_en")


def needs_filter(workspace: Path, *, profile_changed: bool) -> bool:
    """Regenerate after a profile change, or when the file is missing (R14)."""
    return profile_changed or not filter_path(workspace).exists()


def stored_texts(workspace: Path, manifest: Manifest) -> list[tuple[str, str]]:
    """`(origin, text)` of every stored source whose `text.md` can be read, in manifest order."""
    from app.intake.sources import source_text

    out = []
    for entry in manifest.sources:
        try:
            out.append((entry.origin, source_text(workspace, entry.slug)))
        except (OSError, UnicodeDecodeError):
            continue  # a source that cannot be read is left out; the profile still counts
    return out


def budget_sources(
    sources: Iterable[tuple[str, str]], budget: int = CHAR_BUDGET
) -> tuple[list[dict[str, str]], bool]:
    """The sources in order, their texts together within `budget`; whether any was cut."""
    out: list[dict[str, str]] = []
    left = budget
    truncated = False
    for origin, text in sources:
        text = text.strip()
        if not text:
            continue
        if left <= 0:
            truncated = True
            break
        if len(text) > left:
            text, truncated = text[:left], True
        out.append({"origin": origin, "text": text})
        left -= len(text)
    return out, truncated


def build_input(texts: Texts, profile: str, sources: list[dict[str, str]]) -> str:
    payload = {"language": texts.language, "profile": profile, "sources": sources}
    return json.dumps(payload, ensure_ascii=False, indent=1)


def _profile_text(workspace: Path) -> str:
    from app.intake.profile import profile_path

    try:
        return profile_path(workspace).read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def generate(
    model: BaseChatModel,
    texts: Texts,
    workspace: Path,
    *,
    sources: Iterable[tuple[str, str]],
) -> FilterOutcome:
    """Run one generation from `profile.md` and `sources`; never raises for a model failure."""
    from langchain_core.messages import HumanMessage, SystemMessage

    items, truncated = budget_sources(sources)
    messages = [
        SystemMessage(texts.prompt("filter", "generate")),
        HumanMessage(build_input(texts, _profile_text(workspace), items)),
    ]
    try:
        result = model.with_structured_output(FilterFile).invoke(messages)
        if isinstance(result, dict):
            result = FilterFile.model_validate(result)
        if not isinstance(result, FilterFile):
            raise ValueError("no structured output")
    except Exception as exc:  # providers, parsers and validation raise their own types
        return FilterOutcome(truncated=truncated, error=short_error(exc))
    return FilterOutcome(filter=result, truncated=truncated)


def _line(text: str) -> str:
    return " ".join(text.split())


def _prose(text: str) -> str:
    line = _line(text)
    return "\\" + line if line.startswith(("#", ">")) else line


def render(filter_file: FilterFile, texts: Texts) -> str:
    """Markdown: the generated-file header, then one `## ` section per part."""
    parts = [texts.message("filter.header").strip()]
    none = texts.message("filter.none")
    for key in SECTIONS:
        value = getattr(filter_file, key)
        if isinstance(value, str):
            body = value.strip() or none
            # a summary is prose; a line starting with `#` must not become a heading
            body = "\n".join(_prose(p) for p in body.splitlines() if p.strip())
        else:
            lines = [f"- {_line(v)}" for v in value if v.strip()]
            body = "\n".join(lines) or none
        parts.append(f"## {texts.message(f'filter.section.{key}')}\n\n{body}")
    return "\n\n".join(parts) + "\n"


def write_filter(workspace: Path, filter_file: FilterFile, texts: Texts) -> Path:
    """Overwrite `listing_filter_file.md` atomically (0600)."""
    path = filter_path(workspace)
    write_private(path, render(filter_file, texts))
    return path


__all__ = [
    "budget_sources",
    "generate",
    "needs_filter",
    "render",
    "stored_texts",
    "write_filter",
]
