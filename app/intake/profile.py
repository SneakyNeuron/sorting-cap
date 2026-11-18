"""`profile/profile.md`: parse, render, missing items and the merge rule (R12, R13, T7).

Format: a short header, then one `## <title> <!-- item:<id> -->` section per checklist
item in R12 order. Parsing keys on the id comment, so a renamed or translated title
still parses and the title as written is kept. A `## ` section without a known id
comment (or a second section with an id already seen) is an unknown section, kept
verbatim after the known section it followed. The CV section is always rendered from
the manifest (links to the sources flagged `cv`); hand edits to it are overwritten.

Merge rule (R13), per round: an answer to an item's question replaces the item; text
from sources or the introduction fills empty items only. For a filled item the
extraction's relation decides: `match` is ignored, `adds` is appended as a new
paragraph, `contradicts` leaves the item as is and is reported as a conflict.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from pathlib import Path

from app.errors import LocalizedError
from app.files import write_private
from app.intake.checklist import CV_ITEM, ITEMS, is_item_id
from app.messages import short_error
from app.schemas.profile import (
    Conflict,
    ItemSection,
    ItemUpdate,
    MergeResult,
    ProfileDoc,
    UnknownSection,
)
from app.schemas.sources import Manifest
from app.texts import Texts

PROFILE_DIR = "profile"
PROFILE_FILE = "profile.md"
FILTER_FILE = "listing_filter_file.md"

_ID_COMMENT = re.compile(r"<!--\s*item:\s*([A-Za-z0-9_-]+)\s*-->")
_FENCE = re.compile(r"^\s{0,3}(```|~~~)")


class ProfileError(LocalizedError):
    """`profile.md` exists but cannot be read."""


def profile_path(workspace: Path) -> Path:
    return workspace / PROFILE_DIR / PROFILE_FILE


def filter_path(workspace: Path) -> Path:
    return workspace / FILTER_FILE


# --- parse -------------------------------------------------------------------------


def _blocks(text: str) -> tuple[list[str], list[list[str]]]:
    """Split into header lines and `## ` sections (heading first), outside code fences."""
    header: list[str] = []
    sections: list[list[str]] = []
    fence: str | None = None
    for line in text.splitlines():
        match = _FENCE.match(line)
        if match:
            fence = None if fence == match.group(1) else (fence or match.group(1))
        if fence is None and not match and line.startswith("## "):
            sections.append([line])
        elif sections:
            sections[-1].append(line)
        else:
            header.append(line)
    return header, sections


def _strip_blank(lines: list[str]) -> str:
    return "\n".join(lines).strip("\n").rstrip()


def parse(text: str) -> ProfileDoc:
    header, sections = _blocks(text.replace("\r\n", "\n"))
    doc = ProfileDoc(header=_strip_blank(header))
    last: str | None = None
    for lines in sections:
        heading, body = lines[0], lines[1:]
        match = _ID_COMMENT.search(heading)
        item_id = match.group(1) if match else None
        if item_id is None or not is_item_id(item_id) or item_id in doc.items:
            doc.unknown.append(UnknownSection(after=last, raw=_strip_blank(lines)))
            continue
        title = _ID_COMMENT.sub("", heading[3:]).strip()
        doc.items[item_id] = ItemSection(title=title, body=_strip_blank(body))
        last = item_id
    return doc


def load_profile(workspace: Path) -> ProfileDoc:
    """The profile as on disk; an empty one when `profile.md` is missing."""
    path = profile_path(workspace)
    try:
        return parse(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ProfileDoc()
    except (OSError, UnicodeDecodeError) as exc:
        raise ProfileError("profile.unreadable", path=str(path), detail=short_error(exc)) from exc


# --- render ------------------------------------------------------------------------


def _link_label(origin: str) -> str:
    label = " ".join(origin.split())
    return label.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def cv_body(manifest: Manifest) -> str:
    """The CV section: a link to each source flagged `cv`, relative to `profile.md`."""
    return "\n".join(
        f"- [{_link_label(e.origin)}](sources/{e.slug}/)" for e in manifest.sources if e.cv
    )


def render(doc: ProfileDoc, texts: Texts, manifest: Manifest) -> str:
    header = doc.header.strip() or texts.message("profile.header").strip()
    parts = [header]
    for unknown in doc.unknown:
        if unknown.after is None:
            parts.append(unknown.raw)
    for item in ITEMS:
        section = doc.items.get(item.id)  # type: ignore[call-overload]
        title = (section.title if section else "") or item.title(texts)
        body = cv_body(manifest) if item.id == CV_ITEM else (section.body if section else "")
        heading = f"## {title} <!-- item:{item.id} -->"
        parts.append(f"{heading}\n\n{body}" if body else heading)
        parts.extend(u.raw for u in doc.unknown if u.after == item.id)
    return "\n\n".join(parts) + "\n"


def save_profile(workspace: Path, doc: ProfileDoc, texts: Texts, manifest: Manifest) -> Path:
    """Write `profile.md` atomically (0600) under a 0700 `profile/` (T7)."""
    directory = workspace / PROFILE_DIR
    directory.mkdir(mode=0o700, exist_ok=True)
    directory.chmod(0o700)
    path = profile_path(workspace)
    write_private(path, render(doc, texts, manifest))
    return path


# --- items -------------------------------------------------------------------------


def is_filled(doc: ProfileDoc, item_id: str, manifest: Manifest) -> bool:
    """CV: a source flagged `cv` exists; any other item: its section is not empty (R12)."""
    if item_id == CV_ITEM:
        return any(e.cv for e in manifest.sources)
    return bool(doc.body(item_id).strip())


def missing_items(doc: ProfileDoc, manifest: Manifest) -> list[str]:
    """Missing item ids in R12 order (must-know first, then nice-to-have)."""
    return [i.id for i in ITEMS if not is_filled(doc, i.id, manifest)]


def _norm(text: str) -> str:
    return " ".join(text.split()).casefold()


def _set(doc: ProfileDoc, item_id: str, body: str, texts: Texts | None) -> None:
    section = doc.items.get(item_id)  # type: ignore[call-overload]
    if section is None:
        title = texts.message(f"profile.item.{item_id}.title") if texts else ""
        doc.items[item_id] = ItemSection(title=title, body=body)  # type: ignore[index]
    else:
        section.body = body


def merge(
    doc: ProfileDoc,
    *,
    answers: Mapping[str, str] | None = None,
    updates: Iterable[ItemUpdate] = (),
    texts: Texts | None = None,
) -> tuple[ProfileDoc, MergeResult]:
    """Apply one round to a copy of `doc`; `doc` itself is not changed.

    Source and introduction `updates` are applied first, in order, each against the
    profile as updated so far; then `answers` (item id → answer text) replace their
    items, and conflicts about an answered item are dropped (the answer settles it).
    Empty texts and the CV item (rendered from the manifest) are ignored. A filled
    item with no relation is a conflict unless the text is the same as the item's.
    """
    new = doc.model_copy(deep=True)
    conflicts: list[Conflict] = []
    for update in updates:
        text = update.text.strip()
        if not text or update.item_id == CV_ITEM:
            continue
        current = new.body(update.item_id).strip()
        if not current:
            _set(new, update.item_id, text, texts)
        elif _norm(text) == _norm(current) or update.relation == "match":
            continue
        elif update.relation == "adds":
            if _norm(text) not in _norm(current):
                _set(new, update.item_id, f"{current}\n\n{text}", texts)
        else:  # "contradicts", or no relation for a filled item: never overwritten
            conflicts.append(
                Conflict(
                    item_id=update.item_id, current=current, proposed=text, origin=update.origin
                )
            )
    answered: set[str] = set()
    for item_id, answer in (answers or {}).items():
        text = answer.strip()
        if not text or item_id == CV_ITEM or not is_item_id(item_id):
            continue
        _set(new, item_id, text, texts)
        answered.add(item_id)
    changed = [i.id for i in ITEMS if new.body(i.id).strip() != doc.body(i.id).strip()]
    return new, MergeResult(
        changed=changed,  # type: ignore[arg-type]
        conflicts=[c for c in conflicts if c.item_id not in answered],
    )
