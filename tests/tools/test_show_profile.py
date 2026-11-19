"""`-show-profile` (R17, T11)."""

from __future__ import annotations

import builtins
import io
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from helpers import make_ctx

from app.intake.profile import FILTER_FILE, save_profile
from app.intake.sources import save_manifest, sources_dir
from app.schemas.profile import ItemSection, ProfileDoc, ShowProfileInput
from app.schemas.sources import Manifest, ManifestEntry
from app.settings import Settings, save_settings
from app.texts import Texts
from app.tools import default_registry
from app.tools.headless import EXIT_OK, run_headless
from app.tools.show_profile import TOOL, handle

FILTER = "# Filter\n\nBackend roles in Prague.\n"


def manifest() -> Manifest:
    return Manifest(
        sources=[
            ManifestEntry(
                slug="cv-pdf",
                origin="/home/me/cv.pdf",
                sha256="a" * 64,
                type="pdf",
                cv=True,
                extracted=True,
                added_at=datetime(2026, 11, 18, tzinfo=UTC),
            ),
            ManifestEntry(
                slug="example-com",
                origin="https://example.com/me",
                sha256="b" * 64,
                type="url",
                added_at=datetime(2026, 11, 18, tzinfo=UTC),
            ),
        ]
    )


def setup_profile(workspace: Path, lang: str, *, with_filter: bool = True) -> None:
    save_settings(workspace, Settings(language=lang))
    m = manifest()
    save_manifest(workspace, m)
    doc = ProfileDoc(
        items={
            "target_roles": ItemSection(title="", body="Backend developer"),
            "skills": ItemSection(title="", body="Python, SQL"),
        }
    )
    save_profile(workspace, doc, Texts(lang), m)
    for slug in ("cv-pdf", "example-com"):
        folder = sources_dir(workspace) / slug
        folder.mkdir(exist_ok=True)
        (folder / "text.md").write_text("SOURCE-TEXT-MARKER", encoding="utf-8")
    if with_filter:
        (workspace / FILTER_FILE).write_text(FILTER, encoding="utf-8")


def test_tool_is_agent_callable_and_headless():
    registry = default_registry()
    assert registry.get("show-profile") is TOOL
    assert TOOL.agent_callable and not TOOL.chat_only


def test_agent_tool_list_is_exactly_help_tour_show_profile():
    assert [t.name for t in default_registry().agent_tools()] == ["help", "tour", "show-profile"]


@pytest.mark.parametrize(
    ("lang", "words"),
    [
        ("cs", ["Musím vědět", "Cílové pozice", "chybí", "Zdroje", "životopis", "Soubory"]),
        ("en", ["Must know", "Target roles", "missing", "Sources", "CV", "Files"]),
    ],
)
def test_output_in_both_languages(workspace, lang, words):
    setup_profile(workspace, lang)
    ctx, buf = make_ctx(workspace)
    out = handle(ctx, ShowProfileInput())
    by_id = {v.id: v for v in out.items}
    assert list(by_id)[:5] == ["target_roles", "experience", "skills", "education", "languages"]
    assert not by_id["target_roles"].missing and by_id["target_roles"].text == "Backend developer"
    assert by_id["experience"].missing
    assert not by_id["cv"].missing and "sources/cv-pdf/" in by_id["cv"].text
    assert [(s.slug, s.cv) for s in out.sources] == [("cv-pdf", True), ("example-com", False)]
    assert out.filter_text == FILTER
    out.render(ctx)
    text = buf.getvalue()
    for word in words:
        assert word in text, word
    for path in (out.profile_path, out.manifest_path, out.filter_path, out.sources_dir):
        assert path in text
    assert "Backend roles in Prague." in text
    assert "SOURCE-TEXT-MARKER" not in text


def test_empty_workspace(workspace):
    save_settings(workspace, Settings(language="en"))
    ctx, buf = make_ctx(workspace)
    out = handle(ctx, ShowProfileInput())
    assert all(v.missing for v in out.items)
    assert out.sources == [] and out.filter_text is None
    out.render(ctx)
    assert "No sources yet" in buf.getvalue() and "Not generated yet" in buf.getvalue()


def test_corrupt_manifest_still_shows_profile(workspace):
    setup_profile(workspace, "en")
    (sources_dir(workspace) / "manifest.json").write_text("{nope", encoding="utf-8")
    ctx, buf = make_ctx(workspace)
    out = handle(ctx, ShowProfileInput())
    assert out.manifest_error and "manifest.json" in out.manifest_error
    assert {v.id: v for v in out.items}["target_roles"].text == "Backend developer"
    out.render(ctx)
    assert "cannot be read" in " ".join(buf.getvalue().split())


def test_headless_json(workspace):
    setup_profile(workspace, "en")
    out, err = io.StringIO(), io.StringIO()
    assert run_headless(["show-profile", "--json"], stdout=out, stderr=err) == EXIT_OK
    data = json.loads(out.getvalue())
    assert data["filter_text"] == FILTER
    assert [s["slug"] for s in data["sources"]] == ["cv-pdf", "example-com"]
    assert {i["id"]: i["missing"] for i in data["items"]}["skills"] is False
    assert "SOURCE-TEXT-MARKER" not in out.getvalue()


def test_profile_hash_marker_is_not_shown(workspace):
    setup_profile(workspace, "en", with_filter=False)
    sha = "c" * 64
    marked = f"# Filter\n\n<!-- profile-sha256: {sha} -->\n\nBackend roles in Prague.\n"
    (workspace / FILTER_FILE).write_text(marked, encoding="utf-8")
    ctx, buf = make_ctx(workspace)
    out = handle(ctx, ShowProfileInput())
    assert out.filter_text == FILTER
    out.render(ctx)
    assert "profile-sha256" not in buf.getvalue() and sha not in buf.getvalue()
    out_json, err = io.StringIO(), io.StringIO()
    assert run_headless(["show-profile", "--json"], stdout=out_json, stderr=err) == EXIT_OK
    assert json.loads(out_json.getvalue())["filter_text"] == FILTER


def test_generated_filter_file_is_shown_without_the_marker(workspace):
    from app.intake.filter_file import write_filter
    from app.schemas.filter import FilterFile

    setup_profile(workspace, "en", with_filter=False)
    write_filter(
        workspace, FilterFile(summary="Backend roles."), Texts("en"), profile_sha256="d" * 64
    )
    ctx, buf = make_ctx(workspace)
    out = handle(ctx, ShowProfileInput())
    assert "profile-sha256" not in out.filter_text and "Backend roles." in out.filter_text
    assert "\n\n\n" not in out.filter_text


@pytest.mark.parametrize("lang", ["cs", "en"])
def test_headless_text(workspace, lang):
    setup_profile(workspace, lang)
    out, err = io.StringIO(), io.StringIO()
    assert run_headless(["show-profile"], stdout=out, stderr=err) == EXIT_OK
    assert "Backend developer" in out.getvalue()


def test_works_with_source_texts_and_quarantine_unreadable(workspace, monkeypatch):
    setup_profile(workspace, "en")
    quarantine = workspace / "quarantine"
    (quarantine / "item").mkdir(parents=True)
    (quarantine / "item" / "report.json").write_text("{}", encoding="utf-8")
    texts = sorted(sources_dir(workspace).glob("*/text.md"))
    assert len(texts) == 2
    forbidden = [quarantine.resolve(), *(p.resolve() for p in texts)]

    def guarded(path) -> None:
        resolved = Path(os.fsdecode(path)).resolve() if not isinstance(path, int) else None
        if resolved and any(resolved == f or f in resolved.parents for f in forbidden):
            raise AssertionError(f"show-profile touched {resolved}")

    real_open, real_listdir, real_scandir = builtins.open, os.listdir, os.scandir

    def fake_open(file, *a, **kw):
        guarded(file)
        return real_open(file, *a, **kw)

    def fake_listdir(path="."):
        guarded(path)
        return real_listdir(path)

    def fake_scandir(path="."):
        guarded(path)
        return real_scandir(path)

    for p in texts:
        p.chmod(0)
    quarantine.chmod(0)
    monkeypatch.setattr(builtins, "open", fake_open)
    monkeypatch.setattr(io, "open", fake_open)
    monkeypatch.setattr(os, "listdir", fake_listdir)
    monkeypatch.setattr(os, "scandir", fake_scandir)
    try:
        with pytest.raises(AssertionError):  # the trap works
            texts[0].read_text()
        ctx, buf = make_ctx(workspace)
        result = handle(ctx, ShowProfileInput())
        result.render(ctx)
        out, err = io.StringIO(), io.StringIO()
        assert run_headless(["show-profile", "--json"], stdout=out, stderr=err) == EXIT_OK
    finally:
        monkeypatch.undo()
        quarantine.chmod(0o700)
        for p in texts:
            p.chmod(0o600)
    assert len(result.sources) == 2 and result.filter_text == FILTER
    assert "Backend developer" in buf.getvalue()
