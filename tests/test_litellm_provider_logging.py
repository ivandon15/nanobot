import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from nanobot.providers.litellm_provider import LiteLLMProvider
from nanobot.providers.base import LLMResponse

@pytest.mark.asyncio
async def test_logs_model_name_on_request(caplog):
    import logging
    provider = LiteLLMProvider(api_key="test-key", default_model="anthropic/claude-opus-4-5")

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "hello"
    mock_response.choices[0].message.tool_calls = None
    mock_response.choices[0].finish_reason = "stop"
    mock_response.usage.prompt_tokens = 10
    mock_response.usage.completion_tokens = 5
    mock_response.usage.total_tokens = 15

    with patch("nanobot.providers.litellm_provider.acompletion", new=AsyncMock(return_value=mock_response)):
        with caplog.at_level(logging.DEBUG):
            resp = await provider.chat([{"role": "user", "content": "hi"}])

    assert resp.finish_reason == "stop"
    assert "claude-opus-4-5" in caplog.text

@pytest.mark.asyncio
async def test_logs_error_on_exception(caplog):
    import logging
    provider = LiteLLMProvider(api_key="test-key", default_model="anthropic/claude-opus-4-5")

    with patch("nanobot.providers.litellm_provider.acompletion", new=AsyncMock(side_effect=Exception("rate limit"))):
        with caplog.at_level(logging.DEBUG):
            resp = await provider.chat([{"role": "user", "content": "hi"}])

    assert resp.finish_reason == "error"
    assert "rate limit" in caplog.text
