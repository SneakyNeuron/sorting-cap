"""Errors meant for the user, and the one place their texts are resolved.

Every entry point (chat REPL, headless runner, agent tool wrapper) catches
`UserFacingError` and shows `exc.text(texts)`. Messages are plain text, never
Rich markup.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.texts import Texts


class UserFacingError(Exception):
    """An error whose text is shown to the user as is.

    `message` is already a user-facing text in the current language.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self._message = message

    @property
    def message(self) -> str:
        return self.text()

    def text(self, texts: Texts | None = None) -> str:
        return self._message

    def __str__(self) -> str:
        return self.text()


class LocalizedError(UserFacingError):
    """A user-facing error raised where the user's language is not known.

    It carries a message key and parameters; the caller resolves the text in
    its language (`text(texts)`), or the default language without one. A
    `ValidationError` parameter is turned into text by `validation_message`.
    """

    def __init__(self, key: str, **params: Any) -> None:
        Exception.__init__(self, key)
        self.key = key
        self.params = params

    def text(self, texts: Texts | None = None) -> str:
        texts = texts or Texts()
        values = {
            name: validation_message(value, texts) if isinstance(value, ValidationError) else value
            for name, value in self.params.items()
        }
        return texts.message(self.key, **values)


def validation_message(exc: ValidationError, texts: Texts) -> str:
    """`loc: problem; loc: problem` for a Pydantic error, without the input dump.

    A problem with a `validation.<type>` message key is shown in `texts`'
    language (its `ctx` values are the parameters); others keep Pydantic's text.
    """
    parts = []
    for err in exc.errors():
        key = f"validation.{err['type']}"
        if texts.has(key):
            try:
                problem = texts.message(key, **(err.get("ctx") or {}))
            except (KeyError, IndexError):
                problem = err["msg"]
        else:
            problem = err["msg"]
        loc = ".".join(str(p) for p in err["loc"])
        parts.append(f"{loc}: {problem}" if loc else problem)
    return "; ".join(parts)
