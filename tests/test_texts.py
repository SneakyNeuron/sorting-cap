import json

import pytest

from app import texts as texts_mod
from app.paths import PROMPTS_DIR, TEXTS_DIR
from app.texts import LANGUAGES, Texts, TextsError, message_keys, prompt_files


def _files(root):
    return sorted(p.relative_to(root) for p in root.rglob("*") if p.is_file())


def test_languages_have_same_prompt_files():
    assert _files(PROMPTS_DIR / "cs") == _files(PROMPTS_DIR / "en")


def test_languages_have_same_text_files_and_keys():
    assert _files(TEXTS_DIR / "cs") == _files(TEXTS_DIR / "en")
    assert message_keys("cs") == message_keys("en")


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
