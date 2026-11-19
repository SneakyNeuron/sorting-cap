"""Profile model and Markdown I/O, missing items and the merge rule (R12, R13, T7)."""

from __future__ import annotations

import stat
from datetime import UTC, datetime

import pytest

from app.intake.checklist import ITEM_IDS, ITEMS, MUST_KNOW, item, items_in_group, message_keys
from app.intake.profile import (
    ProfileError,
    item_body,
    item_title,
    load_profile,
    merge,
    missing_items,
    parse,
    profile_path,
    render,
    save_profile,
)
from app.schemas.profile import ItemSection, ItemUpdate, ProfileDoc
from app.schemas.sources import Manifest, ManifestEntry
from app.texts import LANGUAGES, Texts

SHA = "a" * 64


def entry(slug="cv-pdf", cv=False, origin="/home/me/cv.pdf", **kw) -> ManifestEntry:
    return ManifestEntry(
        slug=slug,
        origin=origin,
        sha256=kw.pop("sha256", SHA),
        type=kw.pop("type", "pdf"),
        cv=cv,
        added_at=datetime(2026, 11, 18, tzinfo=UTC),
        **kw,
    )


def full_doc() -> ProfileDoc:
    return ProfileDoc(
        items={
            "target_roles": ItemSection(title="Target roles", body="Backend developer"),
            "skills": ItemSection(title="Key skills", body="- Python\n- SQL\n\nAlso Go."),
            "salary": ItemSection(title="Salary range", body="80–100k CZK"),
        }
    )


# --- checklist ---------------------------------------------------------------------


def test_checklist_order_and_groups():
    assert ITEM_IDS == (
        "target_roles",
        "experience",
        "skills",
        "education",
        "languages",
        "cv",
        "salary",
        "contract_type",
        "locations",
        "work_mode",
        "start_date",
        "industries",
    )
    assert [i.id for i in items_in_group("must")] == list(MUST_KNOW)
    assert [i.id for i in ITEMS[:5]] == list(MUST_KNOW)
    assert all(i.group == "nice" for i in ITEMS[5:])
    assert item("cv").group == "nice"


@pytest.mark.parametrize("lang", LANGUAGES)
def test_every_item_has_a_title(lang):
    texts = Texts(lang)
    assert message_keys() == [f"profile.item.{i.id}.title" for i in ITEMS]
    for key in message_keys():
        assert texts.message(key).strip(), key


# --- render / parse ----------------------------------------------------------------


@pytest.mark.parametrize("lang", LANGUAGES)
def test_round_trip(lang):
    texts = Texts(lang)
    manifest = Manifest(sources=[entry(cv=True)])
    text = render(full_doc(), texts, manifest)
    assert text.startswith(texts.message("profile.header").splitlines()[0])
    for i in ITEMS:
        assert f"<!-- item:{i.id} -->" in text
    headings = [line for line in text.splitlines() if line.startswith("## ")]
    assert [h.split("item:")[1].rstrip(" ->") for h in headings] == list(ITEM_IDS)
    parsed = parse(text)
    assert parsed.body("skills") == "- Python\n- SQL\n\nAlso Go."
    assert parsed.body("salary") == "80–100k CZK"
    assert parsed.body("experience") == ""
    assert parsed.items["education"].title == item("education").title(texts)
    assert render(parsed, texts, manifest) == text


def test_empty_doc_renders_localized_titles():
    cs = render(ProfileDoc(), Texts("cs"), Manifest())
    en = render(ProfileDoc(), Texts("en"), Manifest())
    assert "## Cílové pozice <!-- item:target_roles -->" in cs
    assert "## Target roles <!-- item:target_roles -->" in en


def test_hand_edited_title_and_body_kept():
    texts = Texts("en")
    text = render(full_doc(), texts, Manifest())
    edited = text.replace(
        "## Key skills <!-- item:skills -->", "## What I am good at <!-- item:skills -->"
    ).replace("Also Go.", "Also Go and Rust.\n\n### Soft skills\n\nPatience.")
    doc = parse(edited)
    assert doc.items["skills"].title == "What I am good at"
    assert doc.body("skills").endswith("### Soft skills\n\nPatience.")
    again = render(doc, texts, Manifest())
    assert "## What I am good at <!-- item:skills -->" in again
    assert "Also Go and Rust." in again


def test_translated_title_still_parsed():
    en_text = render(full_doc(), Texts("en"), Manifest())
    cs_titles = en_text.replace("## Target roles <!--", "## Cílové pozice <!--").replace(
        "## Key skills <!--", "## Dovednosti <!--"
    )
    doc = parse(cs_titles)
    assert doc.body("target_roles") == "Backend developer"
    assert doc.items["skills"].title == "Dovednosti"
    assert doc.body("skills") == full_doc().body("skills")
    # the title as written is kept, whatever the UI language
    assert "## Cílové pozice <!-- item:target_roles -->" in render(doc, Texts("en"), Manifest())
    # a file written in Czech parses to the same values
    cs_doc = parse(render(full_doc(), Texts("cs"), Manifest()))
    assert {k: v.body for k, v in cs_doc.items.items()} == {
        k: v.body for k, v in parse(en_text).items.items()
    }


def test_unknown_sections_kept_verbatim_in_place():
    texts = Texts("en")
    base = render(full_doc(), texts, Manifest())
    extra = "## My notes\n\nDo not touch.\n  indented  line\n\n```\n## not a heading\n```"
    before = "## Intro without id\n\nHello."
    edited = base.replace(
        "## Work experience <!-- item:experience -->",
        f"{extra}\n\n## Work experience <!-- item:experience -->",
    )
    edited = edited.replace("## Target roles", f"{before}\n\n## Target roles", 1)
    doc = parse(edited)
    assert [u.raw for u in doc.unknown] == [before, extra]
    assert doc.unknown[1].after == "target_roles"
    assert doc.unknown[0].after is None
    out = render(doc, texts, Manifest())
    assert extra in out and before in out
    assert out.index(extra) < out.index("<!-- item:experience -->")
    assert out.index(before) < out.index("<!-- item:target_roles -->")
    assert parse(out) == doc


def test_section_without_id_comment_is_unknown_and_item_missing():
    texts = Texts("en")
    text = render(full_doc(), texts, Manifest()).replace(" <!-- item:salary -->", "")
    doc = parse(text)
    assert "salary" not in doc.items
    assert any(u.raw.startswith("## Salary range\n\n80–100k CZK") for u in doc.unknown)
    assert "salary" in missing_items(doc, Manifest())


def test_duplicate_and_unknown_ids_kept_as_unknown():
    text = (
        "## A <!-- item:skills -->\n\nfirst\n\n"
        "## B <!-- item:skills -->\n\nsecond\n\n"
        "## C <!-- item:hobbies -->\n\nchess\n"
    )
    doc = parse(text)
    assert doc.body("skills") == "first"
    assert [u.raw for u in doc.unknown] == [
        "## B <!-- item:skills -->\n\nsecond",
        "## C <!-- item:hobbies -->\n\nchess",
    ]


def test_header_kept_and_default_when_empty():
    texts = Texts("en")
    doc = parse("# My profile\n\nMine.\n\n## Roles <!-- item:target_roles -->\n\nDev\n")
    assert doc.header == "# My profile\n\nMine."
    assert render(doc, texts, Manifest()).startswith("# My profile\n\nMine.\n\n## Roles")
    assert render(ProfileDoc(), texts, Manifest()).startswith("# Profile")


def test_cv_section_rendered_from_manifest_and_hand_edits_overwritten():
    texts = Texts("en")
    manifest = Manifest(
        sources=[
            entry(cv=True),
            entry(slug="notes-md", origin="notes [old].md", sha256="b" * 64, type="md"),
            entry(slug="web-cv", origin="https://e.com/cv", sha256="c" * 64, type="url", cv=True),
        ]
    )
    doc = parse("## CV <!-- item:cv -->\n\nmy own text\n")
    out = render(doc, texts, manifest)
    cv = out.split("<!-- item:cv -->")[1].split("## ")[0]
    assert "my own text" not in cv
    assert "- [/home/me/cv.pdf](sources/cv-pdf/)" in cv
    assert "- [https://e.com/cv](sources/web-cv/)" in cv
    assert "notes" not in cv


def test_item_title_as_written_or_localized():
    doc = parse("## Moje role <!-- item:target_roles -->\n\nDev\n## <!-- item:skills -->\n")
    assert item_title(doc, "target_roles", Texts("en")) == "Moje role"
    assert item_title(doc, "skills", Texts("en")) == item("skills").title(Texts("en"))
    assert item_title(doc, "salary", Texts("cs")) == item("salary").title(Texts("cs"))


def test_item_body_from_section_or_manifest_for_cv():
    doc = parse("## CV <!-- item:cv -->\n\nmy own text\n## R <!-- item:target_roles -->\n\nDev\n")
    manifest = Manifest(sources=[entry(cv=True)])
    assert item_body(doc, "target_roles", manifest) == "Dev"
    assert item_body(doc, "salary", manifest) == ""
    assert item_body(doc, "cv", manifest) == "- [/home/me/cv.pdf](sources/cv-pdf/)"
    assert item_body(doc, "cv", Manifest()) == ""


def test_crlf_parsed():
    doc = parse("## R <!-- item:target_roles -->\r\n\r\nDev\r\n")
    assert doc.body("target_roles") == "Dev"


# --- files -------------------------------------------------------------------------


def test_missing_file_is_empty_and_save_is_private(workspace):
    assert load_profile(workspace) == ProfileDoc()
    path = save_profile(workspace, full_doc(), Texts("cs"), Manifest())
    assert path == profile_path(workspace)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert load_profile(workspace).body("salary") == "80–100k CZK"


def test_unreadable_file_is_a_localized_error(workspace):
    save_profile(workspace, full_doc(), Texts("en"), Manifest())
    profile_path(workspace).chmod(0)
    try:
        with pytest.raises(ProfileError) as exc:
            load_profile(workspace)
        assert "profile.md" in exc.value.text(Texts("en"))
    finally:
        profile_path(workspace).chmod(0o600)


# --- missing items -----------------------------------------------------------------


def test_missing_items_in_order():
    missing = missing_items(full_doc(), Manifest())
    assert missing == [i for i in ITEM_IDS if i not in ("target_roles", "skills", "salary")]
    assert missing[:3] == ["experience", "education", "languages"]


def test_whitespace_only_section_is_missing():
    doc = parse("## R <!-- item:target_roles -->\n\n   \n\n")
    assert "target_roles" in missing_items(doc, Manifest())


def test_cv_satisfied_by_flagged_source_only():
    doc = parse("## CV <!-- item:cv -->\n\nI have one, honest.\n")
    assert "cv" in missing_items(doc, Manifest(sources=[entry(cv=False)]))
    assert "cv" not in missing_items(ProfileDoc(), Manifest(sources=[entry(cv=True)]))


# --- merge -------------------------------------------------------------------------


def test_answer_replaces_filled_item():
    doc = full_doc()
    new, result = merge(doc, answers={"salary": "120k CZK", "education": "MSc, CTU"})
    assert new.body("salary") == "120k CZK"
    assert new.body("education") == "MSc, CTU"
    assert result.changed == ["education", "salary"]
    assert result.conflicts == []
    assert doc.body("salary") == "80–100k CZK"  # input untouched


def test_source_fills_empty_items_only():
    updates = [
        ItemUpdate(item_id="education", text="BSc", origin="cv-pdf"),
        ItemUpdate(item_id="salary", text="50k", relation="contradicts", origin="cv-pdf"),
    ]
    new, result = merge(full_doc(), updates=updates, texts=Texts("en"))
    assert new.body("education") == "BSc"
    assert new.items["education"].title == "Education"
    assert new.body("salary") == "80–100k CZK"
    assert result.changed == ["education"]
    [conflict] = result.conflicts
    assert (conflict.item_id, conflict.current, conflict.proposed, conflict.origin) == (
        "salary",
        "80–100k CZK",
        "50k",
        "cv-pdf",
    )


def test_adds_appended_match_ignored():
    updates = [
        ItemUpdate(item_id="target_roles", text="Also data engineer", relation="adds"),
        ItemUpdate(item_id="salary", text="around 90k", relation="match"),
    ]
    new, result = merge(full_doc(), updates=updates)
    assert new.body("target_roles") == "Backend developer\n\nAlso data engineer"
    assert new.body("salary") == "80–100k CZK"
    assert result.changed == ["target_roles"] and result.conflicts == []


def test_adds_already_present_not_repeated():
    new, result = merge(
        full_doc(), updates=[ItemUpdate(item_id="skills", text="also go.", relation="adds")]
    )
    assert new.body("skills") == full_doc().body("skills") and result.changed == []


def test_later_update_compared_against_earlier_fill():
    updates = [
        ItemUpdate(item_id="education", text="BSc", origin="a"),
        ItemUpdate(item_id="education", text="BSc", origin="b"),
        ItemUpdate(item_id="education", text="PhD", origin="c"),
    ]
    new, result = merge(ProfileDoc(), updates=updates)
    assert new.body("education") == "BSc"
    assert [(c.proposed, c.origin) for c in result.conflicts] == [("PhD", "c")]


def test_answer_settles_conflict_on_same_item():
    updates = [ItemUpdate(item_id="salary", text="50k", relation="contradicts")]
    new, result = merge(full_doc(), updates=updates, answers={"salary": "60k"})
    assert new.body("salary") == "60k" and result.conflicts == []


def test_empty_texts_and_cv_ignored():
    updates = [
        ItemUpdate(item_id="education", text="   "),
        ItemUpdate(item_id="cv", text="my cv"),
    ]
    new, result = merge(full_doc(), updates=updates, answers={"locations": "", "cv": "/x.pdf"})
    assert new == full_doc() and result.changed == [] and result.conflicts == []


# --- bodies the app writes cannot change the section structure (validation fix) ------


def _round_trip(doc: ProfileDoc) -> ProfileDoc:
    return parse(render(doc, Texts("en"), Manifest()))


def test_answer_with_heading_lines_stays_in_its_item():
    answer = "Acme 2020\n## Projects\nFoo\n# Top\r\n##\u2028## Other\x0bend"
    new, _ = merge(full_doc(), answers={"experience": answer}, texts=Texts("en"))
    back = _round_trip(new)
    assert back.unknown == []
    body = back.body("experience")
    assert body.startswith("Acme 2020") and "Projects" in body and body.endswith("end")
    assert "Foo" in body and "Top" in body and "Other" in body
    assert back.body("salary") == "80–100k CZK"
    assert _round_trip(back) == back  # stable on later rounds


def test_body_with_id_comment_cannot_fill_another_item():
    text = "Rust\n## X <!-- item:salary -->\nfree\n<!-- item:locations -->"
    new, _ = merge(
        ProfileDoc(),
        updates=[ItemUpdate(item_id="skills", text=text)],
        answers={"education": "## Y <!--item: work_mode-->"},
        texts=Texts("en"),
    )
    back = _round_trip(new)
    assert back.unknown == []
    assert back.body("salary") == "" and back.body("work_mode") == ""
    assert back.body("locations") == ""
    assert "free" in back.body("skills") and "Y" in back.body("education")
    assert "<!--" not in back.body("skills") and "<!--" not in back.body("education")


def test_unclosed_fence_in_body_does_not_swallow_later_sections():
    new, _ = merge(full_doc(), answers={"experience": "```\ncode"}, texts=Texts("en"))
    back = _round_trip(new)
    assert back.body("salary") == "80–100k CZK"
    assert "code" in back.body("experience")
    assert _round_trip(back) == back


def test_adds_escapes_only_the_new_text():
    doc = ProfileDoc(items={"skills": ItemSection(title="Key skills", body="# kept as typed")})
    update = ItemUpdate(item_id="skills", text="## More", relation="adds")
    new, _ = merge(doc, updates=[update])
    assert new.body("skills").startswith("# kept as typed\n\n")
    assert _round_trip(new).body("skills") == new.body("skills")
    assert _round_trip(new).unknown == []


def test_hand_written_bodies_parsed_as_before():
    text = "## Key skills <!-- item:skills -->\n\n# not a section\n<!-- item:salary -->\n"
    doc = parse(text)
    assert doc.body("skills") == "# not a section\n<!-- item:salary -->"
    assert parse(render(doc, Texts("en"), Manifest())).body("skills") == doc.body("skills")


def test_merge_reports_which_updates_were_applied():
    updates = [
        ItemUpdate(item_id="experience", text="5 years at Acme"),  # fills an empty item
        ItemUpdate(item_id="target_roles", text="Also data engineer", relation="adds"),
        ItemUpdate(item_id="salary", text="around 90k", relation="match"),
        ItemUpdate(item_id="skills", text="Only Java", relation="contradicts"),
        ItemUpdate(item_id="experience", text="Another", relation=None),  # now a conflict
    ]
    doc = full_doc()
    doc.items.pop("experience", None)
    _, result = merge(doc, updates=updates)
    assert result.applied == [0, 1]
