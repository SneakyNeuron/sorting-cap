import json

import pytest

from app.settings import Settings, SettingsError, load_settings, save_settings, settings_path


def test_defaults(workspace):
    s = load_settings(workspace)
    assert s == Settings()
    assert (
        s.language == "cs" and s.provider is None and s.ollama_base_url == "http://localhost:11434"
    )


def test_hand_edit_picked_up(workspace):
    save_settings(workspace, Settings())
    data = json.loads(settings_path(workspace).read_text())
    data["language"] = "en"
    settings_path(workspace).write_text(json.dumps(data))
    assert load_settings(workspace).language == "en"


def test_invalid_json_names_file_and_is_not_overwritten(workspace):
    path = settings_path(workspace)
    path.write_text("{not json")
    with pytest.raises(SettingsError) as exc:
        load_settings(workspace)
    assert str(path) in str(exc.value)
    assert path.read_text() == "{not json"


def test_invalid_value_names_field(workspace):
    path = settings_path(workspace)
    path.write_text(json.dumps({"language": "de"}))
    with pytest.raises(SettingsError, match="language"):
        load_settings(workspace)
    assert json.loads(path.read_text()) == {"language": "de"}


def test_unknown_provider_rejected(workspace):
    settings_path(workspace).write_text(json.dumps({"provider": "openai"}))
    with pytest.raises(SettingsError, match="provider"):
        load_settings(workspace)


def test_invalid_file_fails_command_without_overwrite(workspace, capsys):
    from app.tools.headless import run_headless

    settings_path(workspace).write_text('{"tour_shown": "maybe"}')
    assert run_headless(["settings", "--language", "en"]) == 1
    assert "tour_shown" in capsys.readouterr().err
    assert settings_path(workspace).read_text() == '{"tour_shown": "maybe"}'


@pytest.mark.parametrize("field", ["ollama_base_url", "openai_base_url"])
@pytest.mark.parametrize("url", ["localhost:11434", "ftp://host/v1", "http://", "file:///x"])
def test_base_urls_need_http_scheme_and_host(field, url):
    with pytest.raises(ValueError, match=field):
        Settings(**{field: url})


def test_settings_file_written_0600(workspace):
    import stat

    save_settings(workspace, Settings())
    assert stat.S_IMODE(settings_path(workspace).stat().st_mode) == 0o600
