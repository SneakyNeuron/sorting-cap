"""`profile/checklist.json`: load, reconcile, apply round outcomes, save (Phase 3 R3, R4, T2)."""

from __future__ import annotations

import json
import os
import stat
from datetime import UTC, datetime

import pytest

from app.intake.checklist import ITEM_IDS
from app.intake.status import (
    apply_changes,
    checklist_path,
    load_status,
    read_status,
    reconcile,
    save_status,
    source_origin,
)
from app.schemas.checklist import ChecklistStatus, ItemStatus, StatusChange
from app.schemas.profile import ItemSection, ProfileDoc
from app.schemas.sources import Manifest, ManifestEntry

T0 = datetime(2026, 11, 1, tzinfo=UTC)
NOW = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)


def doc(**bodies: str) -> ProfileDoc:
    return ProfileDoc(items={k: ItemSection(title="", body=v) for k, v in bodies.items()})


def entry(slug: str, *, cv: bool = False, type: str = "pdf") -> ManifestEntry:
    return ManifestEntry(
        slug=slug, origin=f"/x/{slug}", sha256="a" * 64, type=type, cv=cv, added_at=T0
    )


def stored(**items: ItemStatus) -> ChecklistStatus:
    return ChecklistStatus(items=items)


def st(state: str, *origins: str, note: str | None = None) -> ItemStatus:
    return ItemStatus(state=state, note=note, origins=list(origins), updated_at=T0)


# --- reconcile (R4) ------------------------------------------------------------------


def test_reconcile_covers_every_item_in_checklist_order():
    result = reconcile(ProfileDoc(), Manifest(), stored(), now=NOW)
    assert list(result.items) == list(ITEM_IDS)
    assert all(s.state == "missing" for s in result.items.values())


def test_reconcile_table():
    profile = doc(
        target_roles="Backend",  # stored known: kept
        skills="Python",  # stored partial: kept with its note
        education="MSc",  # no stored status: hand edit
        languages="English",  # stored missing: hand edit
        # experience: stored known, section empty → missing
    )
    old = stored(
        target_roles=st("known", "file:cv-pdf"),
        skills=st("partial", "conversation", note="no levels"),
        languages=st("missing"),
        experience=st("known", "conversation"),
    )
    result = reconcile(profile, Manifest(), old, now=NOW)
    items = result.items
    assert items["target_roles"] == old.items["target_roles"]
    assert items["skills"] == old.items["skills"]
    assert items["education"] == ItemStatus(state="known", origins=["hand_edit"], updated_at=NOW)
    assert items["languages"].state == "known" and items["languages"].origins == ["hand_edit"]
    assert items["languages"].updated_at == NOW
    assert items["experience"].state == "missing" and items["experience"].note is None
    assert items["experience"].origins == []
    assert items["salary"].state == "missing"


def test_reconcile_keeps_an_unchanged_missing_item_as_stored():
    old = stored(salary=st("missing"))
    assert reconcile(ProfileDoc(), Manifest(), old, now=NOW).items["salary"].updated_at == T0


def test_reconcile_cv_follows_the_manifest():
    with_cv = Manifest(sources=[entry("cv-pdf", cv=True)])
    without = Manifest(sources=[entry("cv-pdf")])
    old = stored(cv=st("known", "file:cv-pdf"))
    assert reconcile(ProfileDoc(), with_cv, old, now=NOW).items["cv"] == old.items["cv"]
    assert reconcile(ProfileDoc(), without, old, now=NOW).items["cv"].state == "missing"
    fresh = reconcile(ProfileDoc(), with_cv, stored(), now=NOW).items["cv"]
    assert fresh.state == "known" and fresh.origins == ["hand_edit"]
    # a body under the CV heading does not count (the section comes from the manifest)
    assert reconcile(doc(cv="text"), without, stored(), now=NOW).items["cv"].state == "missing"


def test_reconcile_without_stored_status_uses_origin_profile():
    result = reconcile(
        doc(skills="Python"), Manifest(sources=[entry("cv", cv=True)]), None, now=NOW
    )
    assert result.items["skills"] == ItemStatus(state="known", origins=["profile"], updated_at=NOW)
    assert result.items["cv"].origins == ["profile"]
    assert result.items["experience"].state == "missing"


def test_reconcile_is_pure():
    old = stored(skills=st("missing"))
    before = old.model_dump()
    reconcile(doc(skills="Python"), Manifest(), old, now=NOW)
    assert old.model_dump() == before


# --- apply round outcomes ------------------------------------------------------------


def test_apply_changes_sets_state_note_and_deduplicated_origins():
    base = reconcile(doc(skills="Python"), Manifest(), stored(skills=st("known", "file:a")), now=T0)
    result = apply_changes(
        base,
        [
            StatusChange(item_id="skills", state="partial", note="no levels", origin="file:b"),
            StatusChange(item_id="skills", state="partial", note="no levels", origin="file:a"),
            StatusChange(item_id="salary", state="known", origin="conversation"),
        ],
        now=NOW,
    )
    assert result.items["skills"].state == "partial"
    assert result.items["skills"].note == "no levels"
    assert result.items["skills"].origins == ["file:a", "file:b"]
    assert result.items["skills"].updated_at == NOW
    assert result.items["salary"].origins == ["conversation"]
    assert base.items["skills"].state == "known"  # the input is not changed


def test_known_clears_the_note():
    base = stored(skills=st("partial", "file:a", note="no levels"))
    change = StatusChange(item_id="skills", state="known", origin="conversation")
    item = apply_changes(base, [change], now=NOW).items["skills"]
    assert item.state == "known" and item.note is None
    assert item.origins == ["file:a", "conversation"]


def test_a_change_that_changes_nothing_keeps_the_time():
    base = stored(skills=st("known", "file:a"))
    change = StatusChange(item_id="skills", state="known", origin="file:a")
    assert apply_changes(base, [change], now=NOW).items["skills"].updated_at == T0


def test_origins_deduplicated_on_load():
    item = ItemStatus(state="known", origins=["file:a", "profile", "file:a"], updated_at=T0)
    assert item.origins == ["file:a", "profile"]


@pytest.mark.parametrize("origin", ["elsewhere", "file:", "file:Bad Slug", "web:../x"])
def test_unknown_origins_are_invalid(origin):
    with pytest.raises(ValueError):
        ItemStatus(state="known", origins=[origin], updated_at=T0)


def test_source_origin_by_type():
    assert source_origin(entry("cv-pdf")) == "file:cv-pdf"
    assert source_origin(entry("page", type="html")) == "file:page"
    assert source_origin(entry("example-com", type="url")) == "web:example-com"


# --- load and save -------------------------------------------------------------------


def test_missing_file_loads_as_none_without_note(workspace):
    loaded = load_status(workspace)
    assert loaded.stored is None and not loaded.invalid


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        "[]",
        json.dumps({"version": 2, "items": {}}),
        json.dumps({"version": 1, "items": {"nope": {}}}),
        json.dumps(
            {"version": 1, "items": {"skills": {"state": "great", "updated_at": T0.isoformat()}}}
        ),
        json.dumps(
            {"version": 1, "items": {"skills": {"state": "known", "updated_at": "2026-11-01"}}}
        ),
    ],
)
def test_invalid_file_loads_as_none_with_note(workspace, content):
    path = checklist_path(workspace)
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    loaded = load_status(workspace)
    assert loaded.stored is None and loaded.invalid


def test_unreadable_file_loads_as_invalid(workspace):
    path = checklist_path(workspace)
    path.mkdir(parents=True)  # a directory cannot be read as a file
    assert load_status(workspace).invalid


def test_read_status_rebuilds_an_invalid_file_and_never_writes(workspace):
    path = checklist_path(workspace)
    path.parent.mkdir(parents=True)
    path.write_text("{oops", encoding="utf-8")
    status, invalid = read_status(workspace, doc(skills="Python"), Manifest(), now=NOW)
    assert invalid
    assert status.items["skills"].origins == ["profile"]
    assert path.read_text(encoding="utf-8") == "{oops"


def test_save_writes_0600_atomically_and_round_trips(workspace):
    status = reconcile(doc(skills="Python"), Manifest(), None, now=NOW)
    path = save_status(workspace, status)
    assert path == checklist_path(workspace) == workspace / "profile" / "checklist.json"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert data["items"]["skills"] == {
        "state": "known",
        "note": None,
        "origins": ["profile"],
        "updated_at": "2026-11-19T12:00:00Z",
    }
    assert load_status(workspace).stored == status
    assert [p.name for p in path.parent.iterdir()] == ["checklist.json"]  # no temp file left


def test_save_failure_leaves_the_old_file(workspace, monkeypatch):
    status = reconcile(ProfileDoc(), Manifest(), None, now=NOW)
    path = save_status(workspace, status)
    before = path.read_bytes()

    def broken(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr("app.files.os.replace", broken)
    with pytest.raises(OSError):
        save_status(workspace, reconcile(doc(skills="x"), Manifest(), None, now=NOW))
    assert path.read_bytes() == before
    assert [p.name for p in path.parent.iterdir()] == ["checklist.json"]
