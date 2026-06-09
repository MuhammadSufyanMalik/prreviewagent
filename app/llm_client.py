"""LLM client wrapper.

Currently supports Anthropic. The client is intentionally narrow: it takes a
system prompt and a user message and returns the raw text response. Prompt
construction and JSON parsing live in :mod:`app.review_engine`.
"""

from __future__ import annotations

import logging
from typing import Protocol

logger = logging.getLogger(__name__)


class LLMClient(Protocol):
    def complete(self, system: str, user: str) -> str: ...


class AnthropicClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        max_tokens: int = 4096,
        timeout_seconds: int = 120,
    ) -> None:
        # Imported lazily so the package is only required when the LLM is used.
        from anthropic import Anthropic

        self._client = Anthropic(api_key=api_key, timeout=timeout_seconds)
        self._model = model
        self._max_tokens = max_tokens

    def complete(self, system: str, user: str) -> str:
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        parts = [block.text for block in resp.content if getattr(block, "type", "") == "text"]
        return "".join(parts).strip()


def build_llm_client(provider: str, api_key: str, model: str, max_tokens: int, timeout_seconds: int) -> LLMClient:
    if provider == "anthropic":
        return AnthropicClient(
            api_key=api_key,
            model=model,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
        )
    raise ValueError(f"Unsupported LLM provider: {provider!r}")
