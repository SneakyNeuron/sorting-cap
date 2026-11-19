from helpers import console
from langchain_core.messages import AIMessage

from app.messages import escape_controls, message_text, say, short_error


def test_short_error_first_line_cut_or_class_name():
    assert short_error(RuntimeError("boom\nstack")) == "boom"
    assert short_error(ValueError("")) == "ValueError"
    cut = short_error(RuntimeError("x" * 300), limit=10)
    assert cut == "x" * 9 + "…"


def test_message_text_string_and_blocks():
    assert message_text(AIMessage("hi")) == "hi"
    blocks = AIMessage(
        content=[{"type": "reasoning", "reasoning": "r"}, "a", {"type": "text", "text": "b"}]
    )
    assert message_text(blocks) == "ab"
    assert message_text("plain") == "plain"


def test_escape_controls():
    text = "a\tb\nc\r\x1b[31mred\u202eevil\u2066x\u200bz\x00\u2028end"
    out = escape_controls(text)
    assert out == "a\tb\nc\\x0d\\x1b[31mred\\u202eevil\\u2066x\\u200bz\\x00\\u2028end"
    assert escape_controls("[bold]x[/bold] <b>y</b> :smile:") == "[bold]x[/bold] <b>y</b> :smile:"


def test_say_prints_escaped_plain_text():
    con, buf = console()
    say(con, "[bold]x[/bold] \x1b[31m", "yellow")
    say(con, "")
    assert buf.getvalue() == "[bold]x[/bold] \\x1b[31m\n\n"
