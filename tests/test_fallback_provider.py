import pytest
from unittest.mock import AsyncMock, MagicMock
from nanobot.providers.fallback_provider import FallbackProvider
from nanobot.providers.base import LLMResponse

def _make_provider(content=None, finish_reason="stop", raises=None):
    p = MagicMock()
    p.get_default_model.return_value = "test-model"
    p.api_key = "k"
    p.api_base = None
    if raises:
        p.chat = AsyncMock(side_effect=raises)
    else:
        p.chat = AsyncMock(return_value=LLMResponse(content=content, finish_reason=finish_reason))
    return p

@pytest.mark.asyncio
async def test_returns_primary_on_success():
    p1 = _make_provider("hello")
    p2 = _make_provider("fallback")
    fp = FallbackProvider([(p1, "model-a"), (p2, "model-b")])
    resp = await fp.chat([{"role": "user", "content": "hi"}])
    assert resp.content == "hello"
    assert p1.chat.call_count == 1
    assert p2.chat.call_count == 0

@pytest.mark.asyncio
async def test_retries_once_then_falls_back():
    p1 = _make_provider(finish_reason="error", content="err")
    p2 = _make_provider("fallback ok")
    fp = FallbackProvider([(p1, "model-a"), (p2, "model-b")])
    resp = await fp.chat([{"role": "user", "content": "hi"}])
    assert resp.content == "fallback ok"
    assert p1.chat.call_count == 2  # tried twice
    assert p2.chat.call_count == 1

@pytest.mark.asyncio
async def test_exception_triggers_fallback():
    p1 = _make_provider(raises=Exception("timeout"))
    p2 = _make_provider("recovered")
    fp = FallbackProvider([(p1, "model-a"), (p2, "model-b")])
    resp = await fp.chat([{"role": "user", "content": "hi"}])
    assert resp.content == "recovered"

@pytest.mark.asyncio
async def test_all_fail_returns_error():
    p1 = _make_provider(finish_reason="error", content="err")
    fp = FallbackProvider([(p1, "model-a")])
    resp = await fp.chat([{"role": "user", "content": "hi"}])
    assert resp.finish_reason == "error"
