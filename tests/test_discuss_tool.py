import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from nanobot.agent.tools.discuss import DiscussTool

@pytest.mark.asyncio
async def test_discuss_calls_peers_and_returns_responses():
    peer_a = MagicMock()
    peer_a.process_direct = AsyncMock(return_value="I think X")
    peer_b = MagicMock()
    peer_b.process_direct = AsyncMock(return_value="I think Y")
    tool = DiscussTool(peers={"alice": peer_a, "bob": peer_b})
    result = await tool.execute(question="What do you think?", agent_ids=["alice", "bob"])
    # Peers have replied directly in the group — result tells caller not to repeat
    assert "alice" in result
    assert "bob" in result
    assert "replied directly" in result
    peer_a.process_direct.assert_called_once()
    peer_b.process_direct.assert_called_once()

@pytest.mark.asyncio
async def test_discuss_skips_unknown_agents():
    peer_a = MagicMock()
    peer_a.process_direct = AsyncMock(return_value="response")
    tool = DiscussTool(peers={"alice": peer_a})
    result = await tool.execute(question="Q", agent_ids=["alice", "unknown"])
    assert "alice" in result
    assert "replied directly" in result

@pytest.mark.asyncio
async def test_discuss_handles_timeout():
    async def slow(*args, **kwargs):
        await asyncio.sleep(100)
    peer = MagicMock()
    peer.process_direct = slow
    tool = DiscussTool(peers={"slow_agent": peer}, timeout=0.05)
    result = await tool.execute(question="Q", agent_ids=["slow_agent"])
    assert "timed out" in result.lower() or "timeout" in result.lower()

@pytest.mark.asyncio
async def test_no_peers_returns_message():
    tool = DiscussTool(peers={})
    result = await tool.execute(question="Q", agent_ids=["nobody"])
    assert "no peer" in result.lower() or "available" in result.lower()
