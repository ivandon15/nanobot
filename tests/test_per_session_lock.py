"""Test that process_direct and _dispatch use per-session locks."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

from nanobot.agent.loop import AgentLoop
from nanobot.bus.queue import MessageBus
from nanobot.bus.events import InboundMessage, OutboundMessage


def _make_agent(tmp_path):
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    agent = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=tmp_path,
        model="test-model",
    )
    return agent


@pytest.mark.asyncio
async def test_different_sessions_run_concurrently(tmp_path):
    """Two process_direct calls on different session keys must not block each other."""
    agent = _make_agent(tmp_path)
    order = []

    async def slow_process(msg, session_key=None, on_progress=None):
        order.append(f"start:{session_key}")
        await asyncio.sleep(0.05)
        order.append(f"end:{session_key}")
        return OutboundMessage(channel="cli", chat_id="x", content="ok")

    with patch.object(agent, "_process_message", side_effect=slow_process):
        await asyncio.gather(
            agent.process_direct("hello", session_key="s1"),
            agent.process_direct("world", session_key="s2"),
        )

    # Both should start before either ends (concurrent)
    assert order[0].startswith("start:") and order[1].startswith("start:"), \
        f"Expected concurrent start, got: {order}"


@pytest.mark.asyncio
async def test_same_session_serialized(tmp_path):
    """Two process_direct calls on the same session key must be serialized."""
    agent = _make_agent(tmp_path)
    order = []

    async def slow_process(msg, session_key=None, on_progress=None):
        order.append(f"start:{session_key}")
        await asyncio.sleep(0.05)
        order.append(f"end:{session_key}")
        return OutboundMessage(channel="cli", chat_id="x", content="ok")

    with patch.object(agent, "_process_message", side_effect=slow_process):
        await asyncio.gather(
            agent.process_direct("hello", session_key="same"),
            agent.process_direct("world", session_key="same"),
        )

    # Must be: start, end, start, end (serialized)
    assert order == ["start:same", "end:same", "start:same", "end:same"], \
        f"Expected serialized, got: {order}"
