import json

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
        expected |= {f"profile.item.{item_id}.title", f"profile.item.{item_id}.question"}
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


@pytest.mark.parametrize("lang", LANGUAGES)
def test_tour_and_system_prompt_mention_intake_commands(lang):
    texts = Texts(lang)
    for text in (texts.prewritten("tour"), texts.prompt("chat", "system")):
        assert "/add-information" in text
        assert "/show-profile" in text
