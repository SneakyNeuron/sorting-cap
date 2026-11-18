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
    assert "-x" in Texts("en").message("parse.unknown_command", name="x")
