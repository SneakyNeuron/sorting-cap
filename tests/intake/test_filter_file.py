"""The generated filter file (R14, T9) and its regeneration in the intake round."""

from __future__ import annotations

import json
import stat

import pytest
from helpers import make_ctx, structured_model

from app.intake import filter_file as ff
from app.intake.extract import CHAR_BUDGET
from app.intake.filter_file import filter_path
from app.intake.graph import run_round
from app.intake.profile import save_profile
from app.intake.sources import sources_dir
from app.schemas.filter import FilterFile
from app.schemas.intake import ExtractedValue, Extraction
from app.schemas.profile import ItemSection, ProfileDoc
from app.schemas.sources import Manifest
from app.settings import Settings, save_settings
from app.texts import Texts

FILTER = FilterFile(
    summary="Backend roles in Brno.",
    must_match=["Python", "Brno or remote"],
    nice_to_match=["Django"],
    exclusions=[],
    keywords_cs=["vývojář Python"],
    keywords_en=["Python developer", "backend engineer"],
)


def lang(workspace, language: str):
    save_settings(workspace, Settings(language=language))
    return workspace


def ctx_for(workspace, model):
    ctx, buf = make_ctx(workspace, model_factory=lambda _s, _c: model)
    return ctx, buf


def drop(workspace, name: str, text: str) -> None:
    root = sources_dir(workspace)
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(text, encoding="utf-8")


def roles(text: str) -> Extraction:
    return Extraction(target_roles=ExtractedValue(text=text))


# --- render ----------------------------------------------------------------------


@pytest.mark.parametrize("language", ["cs", "en"])
def test_render_has_header_and_every_section(language):
    texts = Texts(language)
    text = ff.render(FILTER, texts)
    assert text.startswith(texts.message("filter.header"))
    for key in (
        "summary",
        "must_match",
        "nice_to_match",
        "exclusions",
        "keywords_cs",
        "keywords_en",
    ):
        assert f"## {texts.message(f'filter.section.{key}')}" in text
    assert "Backend roles in Brno." in text
    assert "- Brno or remote" in text
    assert "- backend engineer" in text
    # an empty list says so
    exclusions = text.split(f"## {texts.message('filter.section.exclusions')}")[1]
    assert exclusions.strip().startswith(texts.message("filter.none"))


def test_header_says_generated_and_how_to_change():
    for language in ("cs", "en"):
        assert "/add-information" in Texts(language).message("filter.header")


def test_render_keeps_each_criterion_on_one_line():
    text = ff.render(FilterFile(summary="x", must_match=["Python\n\n## injected"]), Texts("en"))
    assert "- Python ## injected" in text
    assert "\n## injected" not in text


def test_write_is_private(workspace):
    path = ff.write_filter(workspace, FILTER, Texts("en"), profile_sha256="a" * 64)
    assert path == filter_path(workspace)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


# --- generation ------------------------------------------------------------------


def test_generate_sends_profile_and_source_texts(workspace):
    lang(workspace, "en")
    doc = ProfileDoc(items={"skills": ItemSection(title="", body="Python, SQL")})
    save_profile(workspace, doc, Texts("en"), Manifest())
    model = structured_model(FILTER)
    outcome = ff.generate(model, Texts("en"), workspace, sources=[("notes.md", "I like Brno.")])
    assert outcome.filter == FILTER and not outcome.truncated
    system, human = model.calls[0]
    assert system.content == Texts("en").prompt("filter", "generate")
    payload = json.loads(human.content)
    assert "Python, SQL" in payload["profile"]
    assert payload["sources"] == [{"origin": "notes.md", "text": "I like Brno."}]
    assert payload["language"] == "en"
    assert model.schemas == [FilterFile]


def test_generate_cuts_source_texts_at_the_budget(workspace):
    model = structured_model(FILTER)
    long = "x" * (CHAR_BUDGET + 50)
    outcome = ff.generate(model, Texts("en"), workspace, sources=[("a", long), ("b", "later")])
    assert outcome.truncated
    payload = json.loads(model.calls[0][1].content)
    assert sum(len(s["text"]) for s in payload["sources"]) == CHAR_BUDGET
    assert [s["origin"] for s in payload["sources"]] == ["a"]


def test_generate_retries_invalid_output(workspace):
    model = structured_model({"summary": ["not", "a", "string"]}, FILTER)
    outcome = ff.generate(model, Texts("en"), workspace, sources=[])
    assert outcome.filter == FILTER
    assert len(model.calls) == 2
    assert model.options == [{"method": None, "include_raw": True}]


def test_generate_invalid_three_times_is_an_error(workspace):
    bad = {"summary": ["x"]}
    outcome = ff.generate(structured_model(bad, bad, bad), Texts("en"), workspace, sources=[])
    assert outcome.filter is None and outcome.error


def test_generate_model_error_is_an_outcome(workspace):
    outcome = ff.generate(
        structured_model(RuntimeError("boom")), Texts("en"), workspace, sources=[]
    )
    assert outcome.filter is None and "boom" in outcome.error


# --- in the round ----------------------------------------------------------------


@pytest.mark.parametrize("language", ["cs", "en"])
def test_round_writes_filter_in_users_language(workspace, language):
    lang(workspace, language)
    drop(workspace, "notes.txt", "I want backend roles.")
    model = structured_model(roles("Backend developer"), FILTER)
    ctx, _ = ctx_for(workspace, model)
    result = run_round(ctx, headless=True)
    assert result.filter_written
    text = filter_path(workspace).read_text(encoding="utf-8")
    assert text.startswith(Texts(language).message("filter.header"))
    payload = json.loads(model.calls[1][1].content)
    assert payload["language"] == language
    assert "Backend developer" in payload["profile"]
    assert payload["sources"][0]["text"].strip() == "I want backend roles."


def test_round_overwrites_filter_when_profile_changes(workspace):
    lang(workspace, "en")
    filter_path(workspace).write_text("old hand edit\n", encoding="utf-8")
    drop(workspace, "notes.txt", "I want backend roles.")
    ctx, _ = ctx_for(workspace, structured_model(roles("Backend developer"), FILTER))
    assert run_round(ctx, headless=True).filter_written
    text = filter_path(workspace).read_text(encoding="utf-8")
    assert "old hand edit" not in text and "Backend roles in Brno." in text

    second = FilterFile(summary="Now also data roles.")
    drop(workspace, "more.txt", "Data engineering too.")
    adds = Extraction(target_roles=ExtractedValue(text="Data engineer", relation="adds"))
    ctx, _ = ctx_for(workspace, structured_model(adds, second))
    assert run_round(ctx, headless=True).filter_written
    assert "Now also data roles." in filter_path(workspace).read_text(encoding="utf-8")


def test_round_without_changes_does_not_regenerate(workspace):
    lang(workspace, "en")
    drop(workspace, "notes.txt", "I want backend roles.")
    ctx, _ = ctx_for(workspace, structured_model(roles("Backend developer"), FILTER))
    run_round(ctx, headless=True)
    before = filter_path(workspace).read_text(encoding="utf-8")

    model = structured_model()  # any model call fails the test
    ctx, _ = ctx_for(workspace, model)
    result = run_round(ctx, headless=True)
    assert not result.filter_written and not result.profile_changed
    assert model.calls == []
    assert filter_path(workspace).read_text(encoding="utf-8") == before


def test_round_regenerates_a_missing_filter(workspace):
    lang(workspace, "en")
    drop(workspace, "notes.txt", "I want backend roles.")
    ctx, _ = ctx_for(workspace, structured_model(roles("Backend developer"), FILTER))
    run_round(ctx, headless=True)
    filter_path(workspace).unlink()

    ctx, _ = ctx_for(workspace, structured_model(FILTER))
    result = run_round(ctx, headless=True)
    assert result.filter_written and not result.profile_changed
    assert filter_path(workspace).exists()


def test_filter_model_error_keeps_old_file_and_notes_it(workspace):
    lang(workspace, "en")
    filter_path(workspace).write_text("previous\n", encoding="utf-8")
    drop(workspace, "notes.txt", "I want backend roles.")
    model = structured_model(roles("Backend developer"), RuntimeError("model down"))
    ctx, _ = ctx_for(workspace, model)
    result = run_round(ctx, headless=True)
    assert not result.filter_written and result.profile_written
    assert filter_path(workspace).read_text(encoding="utf-8") == "previous\n"
    assert [n.kind for n in result.notes] == ["model_error_filter"]
    assert "model down" in result.notes[0].text


def test_filter_input_cut_is_noted(workspace):
    lang(workspace, "en")
    drop(workspace, "long.txt", "y" * (CHAR_BUDGET + 10))
    ctx, _ = ctx_for(workspace, structured_model(roles("Backend developer"), FILTER))
    result = run_round(ctx, headless=True)
    origin = Texts("en").message("intake.origin.filter_sources")
    assert any(n.kind == "truncated" and n.subject == origin for n in result.notes)


# --- staleness: the profile hash in the filter file ---------------------------------


def profile_sha(workspace) -> str:
    import hashlib

    from app.intake.profile import profile_path

    return hashlib.sha256(profile_path(workspace).read_bytes()).hexdigest()


def first_round(workspace) -> None:
    lang(workspace, "en")
    drop(workspace, "notes.txt", "I want backend roles.")
    ctx, _ = ctx_for(workspace, structured_model(roles("Backend developer"), FILTER))
    assert run_round(ctx, headless=True).filter_written


def test_filter_file_records_the_profile_hash(workspace):
    first_round(workspace)
    text = filter_path(workspace).read_text(encoding="utf-8")
    assert f"<!-- profile-sha256: {profile_sha(workspace)} -->" in text
    assert ff.filter_profile_sha(workspace) == profile_sha(workspace)
    assert not ff.needs_filter(workspace, profile_changed=False)


def test_failed_generation_after_a_change_regenerates_next_round(workspace):
    first_round(workspace)
    before = filter_path(workspace).read_text(encoding="utf-8")
    drop(workspace, "more.txt", "Data engineering too.")
    adds = Extraction(target_roles=ExtractedValue(text="Data engineer", relation="adds"))
    ctx, _ = ctx_for(workspace, structured_model(adds, RuntimeError("model down")))
    result = run_round(ctx, headless=True)
    assert result.profile_changed and not result.filter_written
    assert filter_path(workspace).read_text(encoding="utf-8") == before  # untouched

    model = structured_model(FilterFile(summary="Backend and data roles."))
    ctx, _ = ctx_for(workspace, model)
    result = run_round(ctx, headless=True)
    assert not result.profile_changed and result.filter_written
    assert len(model.calls) == 1
    assert "Backend and data roles." in filter_path(workspace).read_text(encoding="utf-8")


def test_hand_edit_to_profile_regenerates(workspace):
    from app.intake.profile import profile_path

    first_round(workspace)
    path = profile_path(workspace)
    path.write_text(path.read_text(encoding="utf-8") + "\nAlso Ostrava.\n", encoding="utf-8")
    model = structured_model(FilterFile(summary="Brno or Ostrava."))
    ctx, _ = ctx_for(workspace, model)
    result = run_round(ctx, headless=True)
    assert result.filter_written and len(model.calls) == 1
    assert "Also Ostrava." in json.loads(model.calls[0][1].content)["profile"]
    assert ff.filter_profile_sha(workspace) == profile_sha(workspace)


def test_matching_hash_makes_no_model_call(workspace):
    first_round(workspace)
    model = structured_model()
    ctx, _ = ctx_for(workspace, model)
    result = run_round(ctx, headless=True)
    assert not result.filter_written and model.calls == []


def test_filter_without_hash_comment_regenerates(workspace):
    import re

    first_round(workspace)
    path = filter_path(workspace)
    path.write_text(re.sub(r"<!-- profile-sha256: [0-9a-f]+ -->\n*", "", path.read_text()))
    assert ff.filter_profile_sha(workspace) is None
    model = structured_model(FILTER)
    ctx, _ = ctx_for(workspace, model)
    assert run_round(ctx, headless=True).filter_written and len(model.calls) == 1


def test_unreadable_filter_file_regenerates(workspace):
    first_round(workspace)
    filter_path(workspace).write_bytes(b"\xff\xfe broken")
    assert ff.filter_profile_sha(workspace) is None
    assert ff.needs_filter(workspace, profile_changed=False)


# --- an unreadable stored text in the filter input ----------------------------------


def test_stored_texts_reports_unreadable_sources(workspace):
    from app.intake.sources import load_manifest

    first_round(workspace)
    drop(workspace, "more.txt", "Data engineering too.")
    adds = Extraction(target_roles=ExtractedValue(text="Data engineer", relation="adds"))
    ctx, _ = ctx_for(workspace, structured_model(adds, FILTER))
    run_round(ctx, headless=True)
    manifest = load_manifest(workspace)
    first, second = manifest.sources
    (sources_dir(workspace) / first.slug / "text.md").write_bytes(b"\xff\xfe")
    texts, unreadable = ff.stored_texts(workspace, manifest)
    assert [origin for origin, _ in texts] == [second.origin]
    assert unreadable == [first.origin]


@pytest.mark.parametrize("language", ["cs", "en"])
def test_unreadable_text_is_noted_in_the_round(workspace, language):
    from app.intake.sources import load_manifest
    from app.schemas.intake import AddInformationOutput
    from app.tools.add_information import summary

    first_round(workspace)
    lang(workspace, language)
    (entry,) = load_manifest(workspace).sources
    (sources_dir(workspace) / entry.slug / "text.md").unlink()
    filter_path(workspace).unlink()  # forces a regeneration
    model = structured_model(FILTER)
    ctx, buf = ctx_for(workspace, model)
    result = run_round(ctx, headless=True)
    assert result.filter_written
    assert json.loads(model.calls[0][1].content)["sources"] == []
    expected = Texts(language).message("intake.note.filter_text_missing", origin=entry.origin)
    notes = [n for n in result.notes if n.kind == "filter_text_missing"]
    assert [(n.subject, n.text) for n in notes] == [(entry.origin, expected)]
    out: AddInformationOutput = summary(ctx, result)
    assert not out.incomplete
    out.render(ctx)
    assert " ".join(expected.split()) in " ".join(buf.getvalue().split())
