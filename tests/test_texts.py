import json
import re
import string

import pytest

from app import texts as texts_mod
from app.paths import PROMPTS_DIR, TEXTS_DIR
from app.texts import (
    LANGUAGES,
    Texts,
    TextsError,
    is_editor_artifact,
    listed_files,
    message_keys,
    prompt_files,
)


def test_languages_have_same_prompt_files():
    assert listed_files(PROMPTS_DIR / "cs") == listed_files(PROMPTS_DIR / "en")


def test_languages_have_same_text_files_and_keys():
    assert listed_files(TEXTS_DIR / "cs") == listed_files(TEXTS_DIR / "en")
    assert message_keys("cs") == message_keys("en")


EDITOR_ARTIFACTS = (
    "chat/.system.md.swp",
    "chat/.system.md.swo",
    "chat/system.md~",
    "chat/.hidden.md",
    ".DS_Store",
    ".cache/notes.md",
)


@pytest.mark.parametrize("name", EDITOR_ARTIFACTS)
def test_editor_artifacts_recognised(name):
    from pathlib import PurePosixPath

    assert is_editor_artifact(PurePosixPath(name))


@pytest.mark.parametrize("name", ["chat/system.md", "intake/extract.md", "messages.json"])
def test_real_files_not_artifacts(name):
    from pathlib import PurePosixPath

    assert not is_editor_artifact(PurePosixPath(name))


def test_parity_ignores_editor_artifacts(tmp_path):
    """Swap, backup and dot files in one language do not break cs/en parity."""
    for lang in LANGUAGES:
        (tmp_path / lang / "chat").mkdir(parents=True)
        (tmp_path / lang / "chat" / "system.md").write_text("x")
    for name in EDITOR_ARTIFACTS:
        path = tmp_path / "cs" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00swap")
    assert listed_files(tmp_path / "cs") == listed_files(tmp_path / "en")
    assert [str(p) for p in listed_files(tmp_path / "en")] == ["chat/system.md"]
    assert [str(p) for p in prompt_files(tmp_path)] == ["cs/chat/system.md", "en/chat/system.md"]


def test_messages_are_non_empty_and_formattable():
    for lang in LANGUAGES:
        data = json.loads((TEXTS_DIR / lang / "messages.json").read_text())
        for key, value in data.items():
            assert value.strip(), (lang, key)


def test_missing_key_fails_loudly():
    with pytest.raises(TextsError, match="nope"):
        Texts("en").message("nope")


def test_missing_file_fails_loudly():
    with pytest.raises(TextsError):
        Texts("en").prompt("chat", "does-not-exist")
    with pytest.raises(TextsError):
        Texts("en").prewritten("does-not-exist")


def test_unknown_language_rejected():
    with pytest.raises(TextsError):
        Texts("de")


def test_missing_catalog(tmp_path):
    texts_mod._catalog.cache_clear()
    with pytest.raises(TextsError, match="catalog"):
        Texts("en", texts_dir=tmp_path).message("x")
    texts_mod._catalog.cache_clear()


def test_every_prompt_file_reachable():
    for rel in prompt_files():
        lang, agent, name = rel.parts[0], rel.parts[1], rel.stem
        assert len(rel.parts) == 3, rel
        assert Texts(lang).prompt(agent, name)


def test_format_values():
    assert "/x" in Texts("en").message("parse.unknown_command", name="x")


def test_one_language_set_everywhere():
    from typing import get_args

    from app.schemas.settings import SettingsInput
    from app.settings import Settings
    from app.texts import LANGUAGE_NAMES
    from app.tools.context import language_choices

    assert set(LANGUAGE_NAMES) == set(LANGUAGES)
    assert [c.value for c in language_choices()] == list(LANGUAGES)
    assert set(get_args(Settings.model_fields["language"].annotation)) == set(LANGUAGES)
    language_input = SettingsInput.model_fields["language"].annotation
    assert set(get_args(get_args(language_input)[0])) == set(LANGUAGES)


def test_dynamic_message_key_families_complete():
    """Keys built at run time (`f"prefix.{value}"`) exist for every value, in both languages."""
    from typing import get_args

    from app.intake.checklist import ITEM_IDS
    from app.intake.filter_file import SECTIONS
    from app.schemas.guard import QuarantineReport
    from app.schemas.intake import NoteKind
    from app.schemas.quarantine import ReleaseQuarantineOutput
    from app.schemas.sources import SourceErrorCode

    expected = set()
    for item_id in ITEM_IDS:
        expected.add(f"profile.item.{item_id}.title")
    expected |= {f"filter.section.{key}" for key in SECTIONS}
    expected |= {f"intake.note.{kind}" for kind in get_args(NoteKind)}
    status = QuarantineReport.model_fields["status"].annotation
    expected |= {f"quarantine.status.{s}" for s in get_args(status)}
    release = ReleaseQuarantineOutput.model_fields["status"].annotation
    expected |= {f"release_quarantine.{s}" for s in get_args(release)}
    # `quarantine_failed` carries the quarantine store's own message, not `sources.<code>`
    expected |= {f"sources.{c}" for c in get_args(SourceErrorCode) if c != "quarantine_failed"}
    for lang in LANGUAGES:
        missing = expected - message_keys(lang)
        assert not missing, (lang, sorted(missing))


def _fields(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name is not None}


def test_message_placeholders_match_across_languages():
    catalogs = {
        lang: json.loads((TEXTS_DIR / lang / "messages.json").read_text()) for lang in LANGUAGES
    }
    en, cs = catalogs["en"], catalogs["cs"]
    differ = sorted(key for key in en if _fields(en[key]) != _fields(cs[key]))
    assert not differ, differ


CODE_TERM = re.compile(r"`([^`\n]+)`")


def test_prompt_code_terms_match_across_languages():
    """Tool names, fields and refusal codes a prompt names are the same in cs and en.

    The only extra a Czech prompt may name is a Czech exit word (`hotovo`).
    """
    czech_words = {w.strip() for w in Texts("cs").message("intake.words.done").split(",")}
    for rel in listed_files(PROMPTS_DIR / "en"):
        en = set(CODE_TERM.findall((PROMPTS_DIR / "en" / rel).read_text(encoding="utf-8")))
        cs = set(CODE_TERM.findall((PROMPTS_DIR / "cs" / rel).read_text(encoding="utf-8")))
        assert cs - czech_words == en - czech_words, (str(rel), sorted(cs ^ en))


# Words that describe `/add-information` as a conversation (Phase 3 R16), and the words of
# the Phase 2 form that must no longer describe it.
CONVERSATION = {
    "en": ("conversation", ("question", "introduction")),
    "cs": ("rozhovor", ("otázk", "představení")),
}


def _intake_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if "/add-information" in line]


@pytest.mark.parametrize("lang", LANGUAGES)
def test_tour_system_prompt_and_help_describe_the_conversation(lang):
    texts = Texts(lang)
    word, stale = CONVERSATION[lang]
    tour, next_steps = texts.prewritten("tour").split("\n## ")[-2:]
    described = {
        "tour": " ".join(_intake_lines(tour)),
        "next steps": " ".join(_intake_lines(next_steps)),
        "system prompt": " ".join(_intake_lines(texts.prompt("chat", "system"))),
        "help": texts.message("tool.add-information.description"),
    }
    for name, text in described.items():
        assert word in text.lower(), (name, text)
    described["help --source"] = texts.message("tool.add-information.arg.source")
    for name, text in described.items():
        for old in stale:
            assert old not in text.lower(), (name, old)
    # The tour tells how to end the conversation (R10).
    exit_word = texts.message("intake.words.done").split(",")[-1].strip()
    assert f"`{exit_word}`" in tour + next_steps


@pytest.mark.parametrize("lang", LANGUAGES)
def test_tour_and_system_prompt_mention_intake_commands(lang):
    texts = Texts(lang)
    for text in (texts.prewritten("tour"), texts.prompt("chat", "system")):
        assert "/add-information" in text
        assert "/show-profile" in text


# Phase 2 keys that only served the scripted dialog (Phase 3 R16).
REMOVED_KEYS = (
    "intake.ask.",
    "intake.words.next",
    '.question"',
    ".question(",
    "intake.origin.intro",
    "intake.origin.answer",
    "model_error_intro",
    "model_error_answer",
    "question_key",
)


def test_scripted_dialog_keys_removed_from_catalogs():
    for lang in LANGUAGES:
        keys = message_keys(lang)
        stale = sorted(
            k
            for k in keys
            if k.startswith("intake.ask.")
            or k == "intake.words.next"
            or (k.startswith("profile.item.") and k.endswith(".question"))
            or k in ("intake.origin.intro", "intake.origin.answer")
            or k in ("intake.note.model_error_intro", "intake.note.model_error_answer")
        )
        assert not stale, (lang, stale)


def test_scripted_dialog_keys_not_referenced_in_code_texts_or_prompts():
    from app.paths import REPO_ROOT

    roots = [REPO_ROOT / "app", TEXTS_DIR, PROMPTS_DIR, REPO_ROOT / "tests", REPO_ROOT / "docs"]
    found = []
    for root in roots:
        for path in root.rglob("*"):
            if path.suffix not in (".py", ".md", ".json") or path.name == "test_texts.py":
                continue
            text = path.read_text(encoding="utf-8")
            for key in REMOVED_KEYS:
                if key in text:
                    found.append((str(path.relative_to(REPO_ROOT)), key))
            if '"question"' in text and path.parent.name == "intake":
                found.append((str(path.relative_to(REPO_ROOT)), '"question"'))
    assert not found, found


@pytest.mark.parametrize("lang", LANGUAGES)
def test_extraction_prompt_has_no_question_input(lang):
    assert "`question`" not in Texts(lang).prompt("intake", "extract")
