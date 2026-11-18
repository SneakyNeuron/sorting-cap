from app import paths


def test_prompts_and_defaults_found():
    assert paths.PROMPTS_DIR.is_dir()
    assert paths.DEFAULTS_DIR.is_dir()
    assert paths.TEXTS_DIR.is_dir()
    assert (paths.REPO_ROOT / "pyproject.toml").is_file()
