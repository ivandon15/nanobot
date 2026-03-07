"""
Smoke test: Operator sends @VicePresident → VicePresident's bot receives and enqueues it.
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from nanobot.channels.feishu import FeishuChannel
from nanobot.bus.queue import MessageBus
from nanobot.bus.events import InboundMessage


def make_two_account_channel():
    from nanobot.config.schema import FeishuConfig, FeishuAccountConfig
    cfg = FeishuConfig(
        enabled=True,
        require_mention=True,
        accounts={
            "Operator": FeishuAccountConfig(app_id="app_op", app_secret="sec_op", name="Operator"),
            "VicePresident": FeishuAccountConfig(app_id="app_vp", app_secret="sec_vp", name="VicePresident"),
        },
    )
    bus = MagicMock(spec=MessageBus)
    bus.publish_inbound = AsyncMock()
    ch = FeishuChannel(cfg, bus)
    ch._bot_open_ids["Operator"] = "ou_operator"
    ch._bot_open_ids["VicePresident"] = "ou_vp"
    ch._clients["Operator"] = MagicMock()
    ch._clients["VicePresident"] = MagicMock()
    return ch


def _make_event(message_id, sender_open_id, sender_type, chat_id, text, mentions=None):
    event = MagicMock()
    event.event.sender.sender_type = sender_type
    event.event.sender.sender_id.open_id = sender_open_id
    event.event.message.message_id = message_id
    event.event.message.chat_id = chat_id
    event.event.message.chat_type = "group"
    event.event.message.message_type = "text"
    event.event.message.content = f'{{"text": "{text}"}}'
    event.event.message.mentions = mentions or []
    return event


@pytest.mark.asyncio
async def test_operator_message_reaches_vp():
    """Operator sends @VicePresident — VicePresident's agent enqueues it."""
    ch = make_two_account_channel()
    ch._add_reaction = AsyncMock(return_value="rxn1")
    ch._record_sent("Operator", "om_op_to_vp")

    mention = MagicMock()
    mention.key = "@_user_1"
    mention.id.open_id = "ou_vp"
    mention.name = "VicePresident"

    data = _make_event("om_op_to_vp", "ou_operator", "bot", "oc_group1",
                       "@VicePresident what do you think?", [mention])

    captured = []
    async def fake_route(msg):
        captured.append(msg)

    ch.agent_pool = MagicMock()
    ch.agent_pool.get_agent.return_value = MagicMock()
    ch.agent_pool.route_inbound = AsyncMock(side_effect=fake_route)

    await ch._on_message(data, "VicePresident")

    ch.agent_pool.route_inbound.assert_called_once()
    inbound: InboundMessage = captured[0]
    assert "what do you think" in inbound.content


@pytest.mark.asyncio
async def test_operator_does_not_process_own_echo():
    """Operator's own sent message is not re-processed by Operator."""
    ch = make_two_account_channel()
    ch._record_sent("Operator", "om_op_echo")

    data = _make_event("om_op_echo", "ou_operator", "bot", "oc_group1", "hello")
    ch.agent_pool = MagicMock()

    await ch._on_message(data, "Operator")
    ch.agent_pool.get_agent.assert_not_called()
