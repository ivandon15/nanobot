from nanobot.config.schema import AgentDefaults, AgentConfig

def test_agent_defaults_has_fallback_fields():
    d = AgentDefaults()
    assert d.model_fallbacks == []
    assert d.image_model is None
    assert d.image_model_fallbacks == []

def test_agent_config_has_fallback_fields():
    a = AgentConfig(id="x", name="X")
    assert a.model_fallbacks is None
    assert a.image_model is None
    assert a.image_model_fallbacks is None

def test_fallback_fields_parse_from_dict():
    d = AgentDefaults(**{
        "model": "anthropic/claude-opus-4-5",
        "modelFallbacks": ["openrouter/claude-opus-4-5", "deepseek/deepseek-chat"],
        "imageModel": "openai/gpt-4o",
        "imageModelFallbacks": ["openrouter/gpt-4o"],
    })
    assert d.model_fallbacks == ["openrouter/claude-opus-4-5", "deepseek/deepseek-chat"]
    assert d.image_model == "openai/gpt-4o"
    assert d.image_model_fallbacks == ["openrouter/gpt-4o"]
