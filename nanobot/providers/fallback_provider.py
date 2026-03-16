"""FallbackProvider — retry-once-then-next-in-chain LLM provider."""
from __future__ import annotations
import asyncio
from typing import Any
from loguru import logger
from nanobot.providers.base import LLMProvider, LLMResponse

_CHAIN_RETRIES = 3          # how many times to retry the whole chain
_CHAIN_RETRY_DELAY = 2.0    # seconds to wait between chain retries


class FallbackProvider(LLMProvider):
    """Wraps a chain of (provider, model) pairs.

    For each call:
      1. Try the current model.
      2. On error/finish_reason=="error": retry once.
      3. Still failing: move to next in chain.
      4. All exhausted: wait and retry the whole chain (up to _CHAIN_RETRIES times).
      5. Still failing: return error response.
    """

    def __init__(self, chain: list[tuple[LLMProvider, str]]):
        if not chain:
            raise ValueError("FallbackProvider requires at least one entry")
        primary_provider, primary_model = chain[0]
        super().__init__(api_key=primary_provider.api_key, api_base=primary_provider.api_base)
        self._chain = chain
        self._primary_model = primary_model

    def get_default_model(self) -> str:
        return self._primary_model

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        kwargs = dict(messages=messages, tools=tools, max_tokens=max_tokens,
                      temperature=temperature, reasoning_effort=reasoning_effort)

        for chain_attempt in range(_CHAIN_RETRIES):
            for idx, (provider, chain_model) in enumerate(self._chain):
                for attempt in range(2):
                    try:
                        resp = await provider.chat(model=chain_model, **kwargs)
                        if resp.finish_reason != "error":
                            return resp
                        if attempt == 0:
                            logger.warning("FallbackProvider: model={} error, retrying", chain_model)
                    except Exception as e:
                        if attempt == 0:
                            logger.warning("FallbackProvider: model={} raised {}, retrying", chain_model, e)
                        else:
                            logger.warning("FallbackProvider: model={} failed twice: {}", chain_model, e)
                            break
                if idx + 1 < len(self._chain):
                    logger.warning("FallbackProvider: falling back from {} to {}",
                                   chain_model, self._chain[idx + 1][1])

            if chain_attempt + 1 < _CHAIN_RETRIES:
                logger.warning(
                    "FallbackProvider: all models failed (attempt {}/{}), retrying in {}s",
                    chain_attempt + 1, _CHAIN_RETRIES, _CHAIN_RETRY_DELAY,
                )
                await asyncio.sleep(_CHAIN_RETRY_DELAY)

        return LLMResponse(content="All models in fallback chain failed.", finish_reason="error")
