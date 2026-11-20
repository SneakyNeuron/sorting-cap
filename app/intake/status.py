"""`profile/checklist.json`: the checklist status store (Phase 3 R3, R4, R12, T2).

The status adds a state (`missing` / `partial` / `known`), a hole note and origins to
each checklist item; `profile.md` and the manifest stay the source of truth for
content. Every read is reconciled with them (`reconcile`, a pure function):

- an empty section (CV: no source flagged `cv`) is `missing`, whatever was stored;
- a filled item with no stored status, or stored as `missing`, is `known` from
  `hand_edit`;
- a stored `partial` or `known` with a filled section is kept.

Without a stored file (the upgrade from Phase 2, or an unreadable or invalid file,
which the caller reports) every filled item is `known` from `profile`.

A round applies what it learned (`apply_changes`) and saves the status after
`profile.md`, whenever it differs from the stored file. `/show-profile` only reads.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from app.files import private_dir, write_private
from app.intake.checklist import ITEMS
from app.intake.profile import PROFILE_DIR, is_filled
from app.schemas.checklist import (
    ORIGIN_HAND_EDIT,
    ORIGIN_PROFILE,
    ChecklistStatus,
    ItemHole,
    ItemStatus,
    StatusChange,
    dedupe,
)
from app.schemas.profile import ItemId, ProfileDoc
from app.schemas.sources import Manifest, ManifestEntry

CHECKLIST_FILE = "checklist.json"


def checklist_path(workspace: Path) -> Path:
    return workspace / PROFILE_DIR / CHECKLIST_FILE


def now_utc() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def source_origin(entry: ManifestEntry) -> str:
    """`web:<slug>` for a fetched URL, `file:<slug>` for any file (HTML files too)."""
    return f"{'web' if entry.type == 'url' else 'file'}:{entry.slug}"


@dataclass(frozen=True)
class LoadedStatus:
    """The stored status as on disk: `None` when missing or `invalid` (unreadable,
    not JSON or not a valid status), so the caller rebuilds it."""

    stored: ChecklistStatus | None
    invalid: bool = False


def load_status(workspace: Path) -> LoadedStatus:
    path = checklist_path(workspace)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return LoadedStatus(None)
    except OSError:
        return LoadedStatus(None, invalid=True)
    try:
        return LoadedStatus(ChecklistStatus.model_validate_json(raw))
    except ValidationError:
        return LoadedStatus(None, invalid=True)


def reconcile(
    doc: ProfileDoc,
    manifest: Manifest,
    stored: ChecklistStatus | None,
    *,
    now: datetime,
) -> ChecklistStatus:
    """The status of every item in checklist order, reconciled with the profile (R4).

    `stored` is `None` when there is no usable stored file. Unchanged items keep their
    stored `updated_at`; `stored` itself is not changed.
    """
    items: dict[ItemId, ItemStatus] = {}
    for item in ITEMS:
        prev = stored.items.get(item.id) if stored else None
        if not is_filled(doc, item.id, manifest):
            if prev is not None and prev.state == "missing" and not prev.note and not prev.origins:
                items[item.id] = prev.model_copy()
            else:
                items[item.id] = ItemStatus(state="missing", updated_at=now)
        elif stored is None:
            items[item.id] = ItemStatus(state="known", origins=[ORIGIN_PROFILE], updated_at=now)
        elif prev is None or prev.state == "missing":
            origins = [*(prev.origins if prev else []), ORIGIN_HAND_EDIT]
            items[item.id] = ItemStatus(state="known", origins=origins, updated_at=now)
        else:
            items[item.id] = prev.model_copy(deep=True)
    return ChecklistStatus(items=items)


def apply_changes(
    status: ChecklistStatus, changes: Iterable[StatusChange], *, now: datetime
) -> ChecklistStatus:
    """A copy of `status` with each change applied in order: the state and note set
    (`known` clears the note), the origin added (a change without one keeps the
    origins). An item whose state, note and origins end up as before keeps its
    `updated_at`."""
    new = status.model_copy(deep=True)
    for change in changes:
        prev = new.items.get(change.item_id)
        note = change.note if change.state == "partial" else None
        origins = list(prev.origins) if prev else []
        if change.origin is not None:
            origins = dedupe([*origins, change.origin])
        if (
            prev is not None
            and prev.state == change.state
            and prev.note == note
            and prev.origins == origins
        ):
            continue
        new.items[change.item_id] = ItemStatus(
            state=change.state, note=note, origins=origins, updated_at=now
        )
    return new


def read_status(
    workspace: Path, doc: ProfileDoc, manifest: Manifest, *, now: datetime | None = None
) -> tuple[ChecklistStatus, bool]:
    """The reconciled status and whether the stored file was invalid; never writes."""
    loaded = load_status(workspace)
    return reconcile(doc, manifest, loaded.stored, now=now or now_utc()), loaded.invalid


def save_status(workspace: Path, status: ChecklistStatus) -> Path:
    """Write `checklist.json` atomically (0600) under a 0700 `profile/` (T2)."""
    private_dir(workspace / PROFILE_DIR)
    path = checklist_path(workspace)
    write_private(path, status.model_dump_json(indent=2) + "\n")
    return path


def holes(status: ChecklistStatus) -> list[ItemHole]:
    """The partial items with their notes, in checklist order."""
    return [
        ItemHole(item_id=i.id, note=s.note)
        for i in ITEMS
        if (s := status.items.get(i.id)) is not None and s.state == "partial"
    ]
