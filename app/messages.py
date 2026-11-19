"""Text from chat models and exceptions, and terminal-safe printing; shared by the REPL,
the agent, verification and the tool outputs' `render`."""

from __future__ import annotations

import unicodedata

from rich.console import Console
from rich.text import Text


def short_error(exc: BaseException, limit: int = 200) -> str:
    """The first line of an exception's text (or its class name), cut to `limit` characters."""
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    return text if len(text) <= limit else text[: limit - 1] + "…"


def message_text(message) -> str:
    """The text of a chat message: string content, or the text blocks of a block list."""
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "".join(parts)


# --- terminal-safe printing ----------------------------------------------------------

_KEEP = frozenset("\n\t")
_ESCAPED_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp", "Cs", "Co", "Cn"})


def _escape_char(ch: str) -> str:
    code = ord(ch)
    if code < 0x100:
        return f"\\x{code:02x}"
    if code < 0x10000:
        return f"\\u{code:04x}"
    return f"\\U{code:08x}"


def escape_controls(text: str) -> str:
    """Text safe to print: control, format (bidi, zero-width) and separator characters
    other than newline and tab are shown as `\\xNN` / `\\uNNNN` escapes."""
    return "".join(
        _escape_char(ch)
        if ch not in _KEEP and unicodedata.category(ch) in _ESCAPED_CATEGORIES
        else ch
        for ch in text
    )


def say(console: Console, text: str, style: str | None = None) -> None:
    """Print `text` as one plain line (no markup, no highlighting), controls escaped."""
    console.print(Text(escape_controls(text), style=style or ""), highlight=False)
