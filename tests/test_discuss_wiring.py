from unittest.mock import MagicMock
from nanobot.agent.pool import AgentPool
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import AgentConfig, AgentsConfig, BindingConfig, Config

def _two_agent_config(tmp_path):
    return Config(
        agents=AgentsConfig(
            defaults={"workspace": str(tmp_path), "model": "anthropic/claude-opus-4-5"},
            list=[
                AgentConfig(id="agent1", name="Alice"),
                AgentConfig(id="agent2", name="Bob"),
            ],
        ),
        bindings=[
            BindingConfig(agentId="agent1", match={"channel": "feishu", "accountId": "app1"}),
            BindingConfig(agentId="agent2", match={"channel": "feishu", "accountId": "app2"}),
        ],
    )

def _factory(model, provider_name):
    p = MagicMock()
    p.get_default_model.return_value = model
    p.api_key = "k"
    p.api_base = None
    return p

def test_get_peer_agents_excludes_self(tmp_path):
    config = _two_agent_config(tmp_path)
    pool = AgentPool(config, MagicMock(spec=MessageBus), _factory)
    pool.register_group_member("oc_group1", "agent1")
    pool.register_group_member("oc_group1", "agent2")
    peers = pool.get_peer_agents("oc_group1", "agent1")
    assert "agent2" in peers
    assert "agent1" not in peers

def test_no_members_returns_empty(tmp_path):
    config = _two_agent_config(tmp_path)
    pool = AgentPool(config, MagicMock(spec=MessageBus), _factory)
    assert pool.get_peer_agents("oc_unknown", "agent1") == {}
