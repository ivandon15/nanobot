"""Test that discuss_with_agents produces visible messages in the group."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from nanobot.agent.tools.discuss import DiscussTool
from nanobot.bus.events import OutboundMessage


@pytest.mark.asyncio
async def test_discuss_publishes_visible_at_mention():
    """discuss_with_agents must publish a visible @mention before calling process_direct."""
    published: list[OutboundMessage] = []

    bus = MagicMock()
    bus.publish_outbound = AsyncMock(side_effect=lambda m: published.append(m))

    peer = MagicMock()
    peer.process_direct = AsyncMock(return_value="I am Operator.")

    tool = DiscussTool(
        peers={"operator": peer},
        bus=bus,
        get_context=lambda: ("feishu", "oc_test", {"account_id": "vp_account"}),
        get_agent_account=lambda ch, aid: "OperatorBot" if aid == "operator" else None,
        timeout=5.0,
    )

    result = await tool.execute(question="Please introduce yourself", agent_ids=["operator"])

    # Visible @mention must have been published
    assert any("@OperatorBot" in (m.content or "") for m in published), \
        f"No @mention published. Messages: {[m.content for m in published]}"

    # process_direct must have been called
    peer.process_direct.assert_called_once()

    # Result must contain peer's response
    assert "I am Operator." in result


@pytest.mark.asyncio
async def test_discuss_reply_published_from_peer_account():
    """Peer's reply must be published with peer's account_id in metadata."""
    published: list[OutboundMessage] = []

    bus = MagicMock()
    bus.publish_outbound = AsyncMock(side_effect=lambda m: published.append(m))

    peer = MagicMock()
    peer.process_direct = AsyncMock(return_value="Hello from Operator.")

    tool = DiscussTool(
        peers={"operator": peer},
        bus=bus,
        get_context=lambda: ("feishu", "oc_test", {"account_id": "vp_account"}),
        get_agent_account=lambda ch, aid: "OperatorBot" if aid == "operator" else None,
        timeout=5.0,
    )

    await tool.execute(question="Hello", agent_ids=["operator"])

    # Check process_direct was called with reply_metadata containing peer's account
    call_kwargs = peer.process_direct.call_args.kwargs
    assert call_kwargs.get("reply_metadata", {}).get("account_id") == "OperatorBot", \
        f"reply_metadata missing peer account_id: {call_kwargs}"
