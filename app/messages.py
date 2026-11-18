"""Text from chat models and exceptions; shared by the REPL, the agent and verification."""

from __future__ import annotations


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
