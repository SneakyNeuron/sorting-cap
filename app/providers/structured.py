"""One structured-output call on a chat model, with retries (T8, T9).

`invoke_structured(model, schema, messages, texts)` returns a validated `schema`
instance or raises:

- The method is the provider's best supported one (`structured_method`): Ollama's
  `json_schema` (the schema is sent as `format`, so decoding is constrained to it);
  `function_calling` for `ChatDeepSeek` (the OpenAI-compatible providers), whose
  `with_structured_output` maps `json_schema` to `function_calling` anyway. Other
  models get their own default.
- The call uses `include_raw=True`. A reply that fails parsing or validation (or has
  no structured output) is retried up to `PARSE_RETRIES` more times, each time with
  the raw reply and the error fed back to the model (the `structured/retry` prompt;
  as a tool message when the reply was a tool call). When the retries are used up,
  `StructuredOutputError` carries the last error.
- Transient errors (connection, timeout, HTTP 429) are retried up to
  `TRANSIENT_ATTEMPTS` attempts in total, waiting `TRANSIENT_DELAYS` between them;
  other exceptions propagate at once. The wait goes through `sleep` (default
  `default_sleep`), which tests replace.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

    from app.texts import Texts

PARSE_RETRIES = 2
TRANSIENT_ATTEMPTS = 3
TRANSIENT_DELAYS = (1.0, 3.0)  # waits before the 2nd and 3rd attempt
FEEDBACK_LIMIT = 2_000  # characters of the error fed back to the model
default_sleep: Callable[[float], None] = time.sleep


class StructuredOutputError(Exception):
    """The model gave no valid structured output after every retry."""


def structured_method(model: Any) -> str | None:
    """The `method` for `with_structured_output` on this model; None for its default."""
    try:
        from langchain_ollama import ChatOllama
    except ImportError:  # pragma: no cover - a declared dependency
        ChatOllama = None  # noqa: N806
    try:
        from langchain_deepseek import ChatDeepSeek
    except ImportError:  # pragma: no cover - a declared dependency
        ChatDeepSeek = None  # noqa: N806
    if ChatOllama is not None and isinstance(model, ChatOllama):
        return "json_schema"
    if ChatDeepSeek is not None and isinstance(model, ChatDeepSeek):
        return "function_calling"
    return None


def is_transient(exc: BaseException) -> bool:
    """A connection problem, a timeout or a rate limit: worth another attempt."""
    import httpx

    if isinstance(exc, ConnectionError | TimeoutError | httpx.TransportError):
        return True
    if getattr(exc, "status_code", None) == 429:  # openai.RateLimitError, ollama.ResponseError
        return True
    try:
        import openai
    except ImportError:  # pragma: no cover - installed with langchain-deepseek
        return False
    return isinstance(exc, openai.APIConnectionError)  # includes APITimeoutError


def _invoke(runnable: Any, messages: list, sleep: Callable[[float], None]) -> Any:
    for attempt in range(TRANSIENT_ATTEMPTS):
        try:
            return runnable.invoke(messages)
        except Exception as exc:
            if attempt + 1 >= TRANSIENT_ATTEMPTS or not is_transient(exc):
                raise
            sleep(TRANSIENT_DELAYS[min(attempt, len(TRANSIENT_DELAYS) - 1)])
    raise AssertionError("unreachable")  # pragma: no cover


def _unpack[T: BaseModel](out: Any, schema: type[T]) -> tuple[T | None, BaseException | None, Any]:
    """`(parsed, error, raw)` from an `include_raw=True` reply (or a plain one)."""
    raw = None
    error: BaseException | None = None
    parsed = out
    if isinstance(out, dict) and "raw" in out:
        raw, parsed, error = out.get("raw"), out.get("parsed"), out.get("parsing_error")
    if error is not None:
        return None, error, raw
    if isinstance(parsed, dict):
        try:
            parsed = schema.model_validate(parsed)
        except ValidationError as exc:
            return None, exc, raw
    if not isinstance(parsed, schema):
        return None, ValueError("no structured output"), raw
    return parsed, None, raw


def _feedback(raw: Any, error: BaseException, texts: Texts) -> list:
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    detail = str(error).strip() or type(error).__name__
    if len(detail) > FEEDBACK_LIMIT:
        detail = detail[: FEEDBACK_LIMIT - 1] + "…"
    text = texts.prompt("structured", "retry").replace("{error}", detail)
    if isinstance(raw, AIMessage):
        if raw.tool_calls:  # every tool call needs its tool message
            return [raw, *(ToolMessage(text, tool_call_id=c["id"]) for c in raw.tool_calls)]
        return [raw, HumanMessage(text)]
    return [HumanMessage(text)]


def invoke_structured[T: BaseModel](
    model: BaseChatModel,
    schema: type[T],
    messages: list,
    texts: Texts,
    *,
    sleep: Callable[[float], None] | None = None,
) -> T:
    """The model's validated output for `messages` (see the module doc for retries)."""
    method = structured_method(model)
    kwargs: dict[str, Any] = {"include_raw": True}
    if method is not None:
        kwargs["method"] = method
    runnable = model.with_structured_output(schema, **kwargs)
    wait = sleep or (lambda seconds: default_sleep(seconds))
    history = list(messages)
    error: BaseException | None = None
    for attempt in range(1 + PARSE_RETRIES):
        parsed, error, raw = _unpack(_invoke(runnable, history, wait), schema)
        if parsed is not None:
            return parsed
        if attempt < PARSE_RETRIES:
            history.extend(_feedback(raw, error, texts))
    raise StructuredOutputError(str(error).strip() or type(error).__name__)


__all__ = [
    "StructuredOutputError",
    "invoke_structured",
    "is_transient",
    "structured_method",
]
