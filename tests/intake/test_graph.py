"""The intake graph with a fake structured-output model (R9, R10, R12, R13, T8, T10)."""

from __future__ import annotations

import pytest
from helpers import ScriptedPrompter, make_ctx, structured_model

from app.intake.checklist import ITEMS
from app.intake.extract import CHAR_BUDGET
from app.intake.graph import run_round
from app.intake.profile import load_profile, profile_path, save_profile
from app.intake.sources import load_manifest, sources_dir
from app.schemas.intake import ExtractedValue, Extraction
from app.schemas.profile import ItemSection, ProfileDoc
from app.schemas.sources import Manifest
from app.texts import Texts

EN = Texts("en")
CS = Texts("cs")


def q(item_id: str, texts: Texts = EN) -> str:
    return texts.message(f"profile.item.{item_id}.question")


def value(text: str, relation=None) -> ExtractedValue:
    return ExtractedValue(text=text, relation=relation)


@pytest.fixture(autouse=True)
def no_filter(monkeypatch):
    """These tests are about the dialog and extraction; the filter node has its own
    tests (`test_filter_file.py`), so here it never calls the model."""
    monkeypatch.setattr("app.intake.graph.needs_filter", lambda *_a, **_k: False)


def ctx_for(workspace, model, *answers):
    prompter = ScriptedPrompter(*answers) if answers else None
    ctx, buf = make_ctx(workspace, prompter, model_factory=lambda _s, _c: model)
    return ctx, prompter, buf


def asked(prompter: ScriptedPrompter) -> list[str]:
    return [question for _kind, question, _ in prompter.asked]


def put_profile(workspace, **bodies: str) -> None:
    doc = ProfileDoc(items={k: ItemSection(title="", body=v) for k, v in bodies.items()})
    save_profile(workspace, doc, EN, Manifest())


# --- dialog --------------------------------------------------------------------------


def test_full_round_answers_skips_and_done(en):
    model = structured_model(
        Extraction(target_roles=value("Backend developer"), skills=value("Python, SQL")),
        Extraction(languages=value("English C1")),
    )
    ctx, prompter, buf = ctx_for(
        en,
        model,
        "",  # no sources
        "",  # no introduction
        "I want backend developer roles; I know Python and SQL",  # target roles
        "next",  # experience
        "",  # education: empty skips too
        "English, C1",  # languages
        "done",  # cv: ends the round
    )
    result = run_round(ctx)
    assert asked(prompter) == [
        EN.message("intake.ask.sources"),
        EN.message("intake.ask.intro"),
        q("target_roles"),
        q("experience"),
        q("education"),  # skills was filled by the first answer, so not asked
        q("languages"),
        q("cv"),
    ]
    assert EN.message("intake.ask.hint") in buf.getvalue()
    doc = load_profile(en)
    assert doc.body("target_roles") == "Backend developer"
    assert doc.body("skills") == "Python, SQL"
    assert doc.body("languages") == "English C1"
    assert doc.body("experience") == ""
    assert not result.cancelled and result.profile_written and result.profile_changed
    assert result.changed == ["target_roles", "skills", "languages"]
    assert result.missing[:2] == ["experience", "education"]
    assert model.queue == []


def test_must_know_before_nice_to_have(en):
    ctx, prompter, _ = ctx_for(en, structured_model(), "", "", *["next"] * len(ITEMS))
    result = run_round(ctx)
    assert asked(prompter)[2:] == [q(i.id) for i in ITEMS]
    assert result.missing == [i.id for i in ITEMS]
    assert profile_path(en).exists()  # a missing profile is (re)created


def test_filled_items_not_asked(en):
    put_profile(en, skills="Python", education="MSc", salary="100k")
    ctx, prompter, _ = ctx_for(en, structured_model(), "", "", *["next"] * 9)
    run_round(ctx)
    questions = asked(prompter)[2:]
    assert q("skills") not in questions and q("education") not in questions
    assert q("salary") not in questions
    assert questions[0] == q("target_roles") and len(questions) == 9


def test_czech_words(workspace):
    model = structured_model()
    ctx, prompter, _ = ctx_for(workspace, model, "", "", "dál", "dal", "hotovo")
    result = run_round(ctx)
    assert asked(prompter)[2:] == [q("target_roles", CS), q("experience", CS), q("skills", CS)]
    assert not result.cancelled and model.calls == []


def test_done_at_sources_still_ingests_and_skips_dialog(en, cwd):
    (cwd / "notes.txt").write_text("I am a Python developer.", encoding="utf-8")
    model = structured_model(Extraction(skills=value("Python")))
    ctx, prompter, _ = ctx_for(en, model, str(cwd / "notes.txt"), "done")
    result = run_round(ctx, cwd=cwd)
    assert len(prompter.asked) == 2
    assert [s.status for s in result.sources] == ["added"]
    assert load_profile(en).body("skills") == "Python"


def test_introduction_fills_empty_items_only(en):
    put_profile(en, skills="Python")
    model = structured_model(
        Extraction(skills=value("Rust", "contradicts"), education=value("BSc"))
    )
    ctx, _, _ = ctx_for(en, model, "", "I studied a BSc and code in Rust", "done")
    result = run_round(ctx)
    doc = load_profile(en)
    assert doc.body("skills") == "Python" and doc.body("education") == "BSc"
    assert [(c.item_id, c.proposed, c.origin) for c in result.conflicts] == [
        ("skills", "Rust", EN.message("intake.origin.intro"))
    ]


# --- sources ------------------------------------------------------------------------


def test_source_fills_adds_matches_and_contradicts(en, cwd):
    put_profile(en, skills="Python", education="MSc Computer Science", languages="Czech native")
    (cwd / "notes.md").write_text("# About me\n\nLots of things.", encoding="utf-8")
    model = structured_model(
        Extraction(
            experience=value("5 years at Acme"),
            skills=value("Go", "adds"),
            education=value("MSc in CS", "match"),
            languages=value("English only", "contradicts"),
        )
    )
    ctx, _, _ = ctx_for(en, model)  # headless: no prompts allowed
    result = run_round(ctx, sources=[str(cwd / "notes.md")], headless=True, cwd=cwd)
    doc = load_profile(en)
    assert doc.body("experience") == "5 years at Acme"
    assert doc.body("skills") == "Python\n\nGo"
    assert doc.body("education") == "MSc Computer Science"
    assert doc.body("languages") == "Czech native"
    assert [(c.item_id, c.current, c.proposed) for c in result.conflicts] == [
        ("languages", "Czech native", "English only")
    ]
    assert result.changed == ["experience", "skills"]
    entry = load_manifest(en).sources[0]
    assert entry.extracted and result.extracted == [entry.slug]
    payload = model.calls[0][1].content
    assert "Lots of things." in payload and '"is_source": true' in payload


def test_extraction_classifies_a_cv(en, cwd):
    (cwd / "resume.txt").write_text("Jan Novák — CV", encoding="utf-8")
    model = structured_model(Extraction(is_cv=True))
    ctx, _, _ = ctx_for(en, model)
    result = run_round(ctx, sources=[str(cwd / "resume.txt")], headless=True, cwd=cwd)
    entry = load_manifest(en).sources[0]
    assert entry.cv and "cv" in result.changed and "cv" not in result.missing
    assert f"(sources/{entry.slug}/)" in profile_path(en).read_text(encoding="utf-8")


def test_dropped_in_file_extracted_headless(en):
    sources_dir(en).mkdir(parents=True)
    (sources_dir(en) / "dropped.txt").write_text("Remote only.", encoding="utf-8")
    ctx, _, _ = ctx_for(en, structured_model(Extraction(work_mode=value("Remote"))))
    result = run_round(ctx, headless=True)
    assert [(s.status, s.dropped_in) for s in result.sources] == [("added", True)]
    assert load_profile(en).body("work_mode") == "Remote"


def test_cancelled_round_leaves_source_unextracted_then_next_round_extracts(en, cwd):
    original = cwd / "notes.txt"
    original.write_text("Ten years of Java.", encoding="utf-8")
    first = structured_model(Extraction(experience=value("10 years Java")))
    ctx, _, _ = ctx_for(en, first, str(original), "", KeyboardInterrupt())
    result = run_round(ctx, cwd=cwd)
    assert result.cancelled and [s.status for s in result.sources] == ["added"]
    assert not profile_path(en).exists()
    assert load_manifest(en).sources[0].extracted is False

    original.unlink()  # the next round reads the stored text.md, not the original
    model = structured_model(Extraction(experience=value("10 years Java")))
    ctx, _, _ = ctx_for(en, model)
    result = run_round(ctx, headless=True)
    assert result.sources == [] and result.extracted == [load_manifest(en).sources[0].slug]
    assert load_manifest(en).sources[0].extracted is True
    assert load_profile(en).body("experience") == "10 years Java"
    assert "Ten years of Java." in model.calls[0][1].content


def test_failed_source_extraction_retried_next_headless_round(en, cwd):
    (cwd / "notes.txt").write_text("SQL wizard.", encoding="utf-8")
    ctx, _, _ = ctx_for(en, structured_model(RuntimeError("model down")))
    result = run_round(ctx, sources=[str(cwd / "notes.txt")], headless=True, cwd=cwd)
    assert [n.kind for n in result.notes] == ["model_error_source"]
    assert "model down" in result.notes[0].text
    assert result.extracted == [] and load_manifest(en).sources[0].extracted is False
    (cwd / "notes.txt").unlink()

    model = structured_model(Extraction(skills=value("SQL")))
    ctx, _, _ = ctx_for(en, model)
    result = run_round(ctx, headless=True)
    assert result.notes == [] and result.sources == []
    assert load_manifest(en).sources[0].extracted is True
    assert load_profile(en).body("skills") == "SQL"


def test_cancelled_then_chat_round_extracts(en, cwd):
    (cwd / "notes.txt").write_text("Prague.", encoding="utf-8")
    ctx, _, _ = ctx_for(en, structured_model(Extraction()), str(cwd / "notes.txt"), "", EOFError())
    assert run_round(ctx, cwd=cwd).cancelled
    model = structured_model(Extraction(locations=value("Prague")))
    ctx, _, _ = ctx_for(en, model, "", "done")
    result = run_round(ctx)
    assert load_manifest(en).sources[0].extracted is True
    assert result.extracted == [load_manifest(en).sources[0].slug]
    assert load_profile(en).body("locations") == "Prague"


def test_cv_question_with_a_path_flags_the_source(en, cwd):
    (cwd / "cv.txt").write_text("Jan Novák, Python developer", encoding="utf-8")
    model = structured_model(Extraction(skills=value("Python")))
    ctx, prompter, _ = ctx_for(en, model, "", "", *["next"] * 5, str(cwd / "cv.txt"), "done")
    result = run_round(ctx, cwd=cwd)
    assert asked(prompter)[7] == q("cv")
    entry = load_manifest(en).sources[0]
    assert entry.cv and entry.extracted
    assert [s.status for s in result.sources] == ["added"]
    assert "cv" in result.changed and "cv" not in result.missing
    assert load_profile(en).body("skills") == "Python"
    assert f"(sources/{entry.slug}/)" in profile_path(en).read_text(encoding="utf-8")


def test_long_source_is_cut_and_noted(en, cwd):
    (cwd / "long.txt").write_text("x" * (CHAR_BUDGET + 1000), encoding="utf-8")
    model = structured_model(Extraction())
    ctx, _, _ = ctx_for(en, model)
    result = run_round(ctx, sources=[str(cwd / "long.txt")], headless=True, cwd=cwd)
    assert [n.kind for n in result.notes] == ["truncated"]
    assert str(cwd / "long.txt") in result.notes[0].text
    assert '"' + "x" * CHAR_BUDGET + '"' in model.calls[0][1].content


# --- model errors and cancellation ----------------------------------------------------


def test_model_error_on_answer_keeps_text_verbatim(en):
    model = structured_model(RuntimeError("timeout"))
    ctx, _, _ = ctx_for(en, model, "", "", "  Data engineer, maybe ML  ", "done")
    result = run_round(ctx)
    assert load_profile(en).body("target_roles") == "Data engineer, maybe ML"
    assert [n.kind for n in result.notes] == ["model_error_answer"]
    assert result.changed == ["target_roles"]


def test_answer_without_value_for_its_item_kept_verbatim(en):
    model = structured_model(Extraction(skills=value("Excel")))
    ctx, _, _ = ctx_for(en, model, "", "", "Analyst; I use Excel", "done")
    run_round(ctx)
    doc = load_profile(en)
    assert doc.body("target_roles") == "Analyst; I use Excel"
    assert doc.body("skills") == "Excel"


def test_answer_replaces_a_filled_item(en):
    put_profile(en, skills="Python")
    model = structured_model(Extraction(target_roles=value("PM")))
    ctx, _, _ = ctx_for(en, model, "", "", "PM", "done")
    run_round(ctx)
    assert load_profile(en).body("target_roles") == "PM"
    assert load_profile(en).body("skills") == "Python"


@pytest.mark.parametrize("stop", [KeyboardInterrupt(), EOFError()])
def test_ctrl_c_mid_round_leaves_profile_unchanged(en, stop):
    put_profile(en, skills="Python")
    path = profile_path(en)
    path.write_text(path.read_text(encoding="utf-8") + "\n## My notes\n\nHand text.\n")
    before = path.read_bytes()
    model = structured_model(Extraction(target_roles=value("Backend")))
    ctx, _, _ = ctx_for(en, model, "", "", "Backend", stop)
    result = run_round(ctx)
    assert result.cancelled and result.changed == [] and not result.profile_written
    assert path.read_bytes() == before


def test_unchanged_round_does_not_rewrite_profile(en):
    put_profile(en, skills="Python")
    path = profile_path(en)
    path.write_text(path.read_text(encoding="utf-8") + "\n\n\n")
    before = path.read_bytes()
    ctx, _, _ = ctx_for(en, structured_model(), "", "", "done")
    result = run_round(ctx)
    assert not result.profile_written and not result.profile_changed
    assert path.read_bytes() == before


def test_ctrl_c_during_filter_reports_the_written_round(en, monkeypatch):
    """`write` saved `profile.md` before the filter call; the result says so (validation fix)."""
    monkeypatch.setattr("app.intake.graph.needs_filter", lambda *_a, **_k: True)

    def interrupted(*_a, **_k):
        raise KeyboardInterrupt

    monkeypatch.setattr("app.intake.graph.generate", interrupted)
    model = structured_model(Extraction(target_roles=value("Backend")))
    ctx, _, _ = ctx_for(en, model, "", "", "Backend", "done")
    result = run_round(ctx)
    assert not result.cancelled and result.profile_written and result.changed == ["target_roles"]
    assert not result.filter_written
    assert [n.kind for n in result.notes] == ["filter_interrupted"]
    assert result.notes[0].text == EN.message("intake.note.filter_interrupted")
    assert load_profile(en).body("target_roles") == "Backend"
