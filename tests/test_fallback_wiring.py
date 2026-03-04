from unittest.mock import MagicMock
import pytest
from nanobot.agent.pool import AgentPool
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import AgentsConfig, Config

def _make_config(tmp_path, model_fallbacks=None, image_model=None, image_model_fallbacks=None):
    return Config(
        agents=AgentsConfig(
            defaults={
                "workspace": str(tmp_path),
                "model": "anthropic/claude-opus-4-5",
                "modelFallbacks": model_fallbacks or [],
                "imageModel": image_model,
                "imageModelFallbacks": image_model_fallbacks or [],
            },
            list=[],
        )
    )

def _factory(model, provider_name):
    p = MagicMock()
    p.get_default_model.return_value = model
    p.api_key = "k"
    p.api_base = None
    return p

def test_no_fallbacks_wraps_in_fallback_provider(tmp_path):
    from nanobot.providers.fallback_provider import FallbackProvider
    config = _make_config(tmp_path)
    pool = AgentPool(config, MagicMock(spec=MessageBus), _factory)
    agent = pool.get_default_agent()
    assert isinstance(agent.provider, FallbackProvider)
    assert agent.image_provider is None

def test_fallbacks_build_chain(tmp_path):
    from nanobot.providers.fallback_provider import FallbackProvider
    config = _make_config(
        tmp_path,
        model_fallbacks=["openrouter/claude-opus-4-5"],
        image_model="openai/gpt-4o",
    )
    pool = AgentPool(config, MagicMock(spec=MessageBus), _factory)
    agent = pool.get_default_agent()
    assert isinstance(agent.provider, FallbackProvider)
    assert len(agent.provider._chain) == 2
    assert isinstance(agent.image_provider, FallbackProvider)
