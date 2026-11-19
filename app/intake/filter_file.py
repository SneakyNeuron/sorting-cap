"""`listing_filter_file.md`: generated from the profile and the source texts (R14, T9).

One structured-output call on the chat model from settings, into `FilterFile`; the
system prompt is the `filter/generate` prompt (read through `app/texts.py`), the user
message is JSON with `profile.md`, the stored source texts and the user's language,
so the texts stay data. The source texts together are cut at the same character
budget as extraction (T8); `profile.md` is sent whole. Code renders the Markdown with
the generated-file header and writes it with `write_private`, overwriting the old one.

The header records the SHA-256 of the `profile.md` bytes the filter was generated
from (`<!-- profile-sha256: … -->`). It is regenerated after a round that changed the
profile, when the file is missing or unreadable, has no hash, or its hash differs
from the current `profile.md` (`needs_filter`), so a failed generation or a hand edit
of `profile.md` is caught up on the next round. Any model failure is returned as
`FilterOutcome.error`; the caller keeps the previous file untouched and notes it. The
generation model gets no tools.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from app.files import write_private
from app.intake.extract import CHAR_BUDGET
from app.intake.profile import filter_path
from app.messages import short_error
from app.providers.structured import invoke_structured
from app.schemas.filter import FilterFile, FilterOutcome
from app.texts import Texts

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

    from app.schemas.sources import Manifest

SECTIONS = ("summary", "must_match", "nice_to_match", "exclusions", "keywords_cs", "keywords_en")


_HASH_COMMENT = "<!-- profile-sha256: {sha} -->"
_HASH_RE = re.compile(r"<!-- profile-sha256: ([0-9a-f]{64}) -->")
_HASH_STRIP_RE = re.compile(r"^[ \t]*<!-- profile-sha256: [0-9a-f]*[ \t]*-->[ \t]*(?:\r?\n)*", re.M)


def filter_profile_sha(workspace: Path) -> str | None:
    """The profile hash recorded in the filter file; None if missing, unreadable or absent."""
    try:
        text = filter_path(workspace).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    found = _HASH_RE.search(text)
    return found.group(1) if found else None


def strip_profile_hash(text: str) -> str:
    """The filter file's text without the internal `profile-sha256` marker (and the
    blank lines after it), for showing to the user."""
    return _HASH_STRIP_RE.sub("", text)


def needs_filter(workspace: Path, *, profile_changed: bool) -> bool:
    """Regenerate after a profile change, or when the file is missing, has no readable
    hash, or was generated from a different `profile.md` (R14)."""
    if profile_changed:
        return True
    recorded = filter_profile_sha(workspace)
    return recorded is None or recorded != hashlib.sha256(_profile_bytes(workspace)).hexdigest()


def stored_texts(workspace: Path, manifest: Manifest) -> tuple[list[tuple[str, str]], list[str]]:
    """`(origin, text)` of every stored source whose `text.md` can be read, in manifest
    order, and the origins of those that cannot (left out; the caller notes them)."""
    from app.intake.sources import source_text

    out = []
    unreadable = []
    for entry in manifest.sources:
        try:
            out.append((entry.origin, source_text(workspace, entry.slug)))
        except (OSError, UnicodeDecodeError):
            unreadable.append(entry.origin)
    return out, unreadable


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


def _profile_bytes(workspace: Path) -> bytes:
    from app.intake.profile import profile_path

    try:
        return profile_path(workspace).read_bytes()
    except FileNotFoundError:
        return b""


def _profile(workspace: Path) -> tuple[str, str]:
    """`profile.md` as text (empty if missing) and the SHA-256 of its bytes."""
    data = _profile_bytes(workspace)
    return data.decode("utf-8"), hashlib.sha256(data).hexdigest()


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
    profile, sha = _profile(workspace)
    messages = [
        SystemMessage(texts.prompt("filter", "generate")),
        HumanMessage(build_input(texts, profile, items)),
    ]
    try:
        result = invoke_structured(model, FilterFile, messages, texts)
    except Exception as exc:  # providers, parsers and validation raise their own types
        return FilterOutcome(truncated=truncated, error=short_error(exc))
    return FilterOutcome(filter=result, truncated=truncated, profile_sha256=sha)


def _line(text: str) -> str:
    return " ".join(text.split())


def _prose(text: str) -> str:
    line = _line(text)
    return "\\" + line if line.startswith(("#", ">")) else line


def render(filter_file: FilterFile, texts: Texts, profile_sha256: str | None = None) -> str:
    """Markdown: the generated-file header (with the profile hash, if given), then one
    `## ` section per part."""
    parts = [texts.message("filter.header").strip()]
    if profile_sha256:
        parts.append(_HASH_COMMENT.format(sha=profile_sha256))
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


def write_filter(
    workspace: Path, filter_file: FilterFile, texts: Texts, *, profile_sha256: str
) -> Path:
    """Overwrite `listing_filter_file.md` atomically (0600), recording the profile hash."""
    path = filter_path(workspace)
    write_private(path, render(filter_file, texts, profile_sha256))
    return path


__all__ = [
    "budget_sources",
    "filter_profile_sha",
    "generate",
    "needs_filter",
    "render",
    "strip_profile_hash",
    "stored_texts",
    "write_filter",
]
