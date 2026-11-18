import pytest
from pydantic import ValidationError

from app.errors import LocalizedError, UserFacingError, validation_message
from app.providers.base import SetupAborted
from app.settings import Settings, SettingsError
from app.texts import Texts
from app.tools.context import ToolError
from app.tools.registry import ParseError
from app.workspace import WorkspaceError


def _error(**data) -> ValidationError:
    with pytest.raises(ValidationError) as exc:
        Settings.model_validate(data)
    return exc.value


@pytest.mark.parametrize(
    "cls", [ToolError, SetupAborted, ParseError, SettingsError, WorkspaceError]
)
def test_every_user_facing_error_shares_one_base(cls):
    assert issubclass(cls, UserFacingError)


def test_plain_message_is_kept():
    exc = ToolError("it [/x] broke")
    assert exc.message == exc.text(Texts("en")) == str(exc) == "it [/x] broke"


def test_localized_error_resolved_in_callers_language_default_cs():
    exc = LocalizedError("workspace.not_directory", path="/w")
    assert exc.text(Texts("en")) == "The workspace /w is not a directory."
    assert exc.text() == Texts("cs").message("workspace.not_directory", path="/w")
    assert str(exc) == exc.message == exc.text()


def test_validation_message_is_short_and_has_no_dump():
    text = validation_message(_error(language="de", tour_shown="maybe"), Texts("en"))
    assert text == "language: expected 'cs' or 'en'; tour_shown: expected true or false"
    assert "pydantic" not in text and "input_value" not in text and "\n" not in text


def test_validation_message_localizes_custom_errors():
    exc = _error(provider="foo", ollama_base_url="localhost:1")
    en = validation_message(exc, Texts("en"))
    cs = validation_message(exc, Texts("cs"))
    assert "provider: unknown provider 'foo'; known: ollama" in en
    assert "ollama_base_url: expected an http:// or https:// URL" in en
    assert "provider: neznámý poskytovatel 'foo'" in cs
    assert "unknown" not in cs and "expected" not in cs


def test_validation_message_falls_back_to_pydantic_text():
    from decimal import Decimal

    from pydantic import BaseModel

    class M(BaseModel):
        n: Decimal  # `validation.decimal_parsing` has no catalog entry

    with pytest.raises(ValidationError) as exc:
        M(n="x")
    assert validation_message(exc.value, Texts("en")) == f"n: {exc.value.errors()[0]['msg']}"
