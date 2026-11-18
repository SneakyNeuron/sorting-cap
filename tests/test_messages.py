from langchain_core.messages import AIMessage

from app.messages import message_text, short_error


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
