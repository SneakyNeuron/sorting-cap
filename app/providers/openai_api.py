"""Chat models for OpenAI-compatible endpoints via `ChatDeepSeek`.

`ChatOpenAI` drops `reasoning_content`; `ChatDeepSeek` keeps it in
`additional_kwargs["reasoning_content"]`. Key and base URL are always passed
explicitly so `DEEPSEEK_API_KEY` / `DEEPSEEK_API_BASE` never apply.
"""

from __future__ import annotations

from typing import Any

from app.providers.remote import KEYLESS_PLACEHOLDER


def openai_compatible_model(
    *, model: str, api_base: str, api_key: str | None, http_client: Any = None
):
    from langchain_deepseek import ChatDeepSeek
    from pydantic import SecretStr

    kwargs: dict[str, Any] = {}
    if http_client is not None:
        kwargs["http_client"] = http_client
    return ChatDeepSeek(
        model=model,
        api_base=api_base.rstrip("/"),
        api_key=SecretStr(api_key or KEYLESS_PLACEHOLDER),
        max_retries=1,
        **kwargs,
    )
