import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from nanobot.channels.feishu import FeishuChannel, _should_use_card
from nanobot.bus.events import OutboundMessage


def make_channel():
    from nanobot.config.schema import FeishuConfig
    from nanobot.bus.queue import MessageBus
    cfg = FeishuConfig(enabled=True, app_id="aid", app_secret="asec")
    bus = MagicMock(spec=MessageBus)
    return FeishuChannel(cfg, bus)


def _make_mock_client(app_id="aid", app_secret="asec"):
    mock_client = MagicMock()
    mock_client.config.app_id = app_id
    mock_client.config.app_secret = app_secret
    return mock_client


def test_fetch_bot_open_id_returns_open_id():
    ch = make_channel()
    mock_client = _make_mock_client()

    with patch("nanobot.channels.feishu._requests") as mock_requests:
        mock_token_resp = MagicMock()
        mock_token_resp.json.return_value = {"tenant_access_token": "tok123"}
        mock_bot_resp = MagicMock()
        mock_bot_resp.json.return_value = {"code": 0, "bot": {"open_id": "ou_botid123"}}
        mock_requests.post.return_value = mock_token_resp
        mock_requests.get.return_value = mock_bot_resp

        result = ch._fetch_bot_open_id_sync("aid", "asec")
    assert result == "ou_botid123"


def test_fetch_bot_open_id_returns_none_on_failure():
    ch = make_channel()
    mock_client = _make_mock_client()

    with patch("nanobot.channels.feishu._requests") as mock_requests:
        mock_token_resp = MagicMock()
        mock_token_resp.json.return_value = {"tenant_access_token": "tok123"}
        mock_bot_resp = MagicMock()
        mock_bot_resp.json.return_value = {"code": 99, "msg": "error"}
        mock_requests.post.return_value = mock_token_resp
        mock_requests.get.return_value = mock_bot_resp

        result = ch._fetch_bot_open_id_sync("aid", "asec")
    assert result is None


# Task 3 tests
from nanobot.channels.feishu import _check_bot_mentioned, _strip_bot_mention


def test_check_bot_mentioned_finds_bot_in_mentions():
    mentions = [{"key": "@_user_1", "id": {"open_id": "ou_bot123"}, "name": "MyBot"}]
    assert _check_bot_mentioned(mentions, "ou_bot123") is True


def test_check_bot_mentioned_returns_false_when_not_mentioned():
    mentions = [{"key": "@_user_1", "id": {"open_id": "ou_other"}, "name": "Other"}]
    assert _check_bot_mentioned(mentions, "ou_bot123") is False


def test_check_bot_mentioned_returns_false_with_no_bot_id():
    assert _check_bot_mentioned([], None) is False


def test_strip_bot_mention_removes_at_name():
    result = _strip_bot_mention("@MyBot hello world", [{"name": "MyBot", "key": "@_user_1"}])
    assert "MyBot" not in result
    assert "hello world" in result


def test_group_allow_from_blocks_unlisted_group():
    import asyncio
    from nanobot.config.schema import FeishuConfig
    from nanobot.bus.queue import MessageBus

    cfg = FeishuConfig(
        enabled=True, app_id="aid", app_secret="asec",
        group_allow_from=["oc_allowed"],
    )
    bus = MagicMock(spec=MessageBus)
    bus.publish_inbound = AsyncMock()
    ch = FeishuChannel(cfg, bus)

    event = MagicMock()
    event.event.sender.sender_type = "user"
    event.event.sender.sender_id.open_id = "ou_user1"
    event.event.message.message_id = "msg1"
    event.event.message.chat_id = "oc_blocked"
    event.event.message.chat_type = "group"
    event.event.message.message_type = "text"
    event.event.message.content = '{"text": "hello"}'
    event.event.message.mentions = []

    asyncio.run(ch._on_message(event, "default"))
    bus.publish_inbound.assert_not_called()


# Task 4 tests
def test_should_use_card_detects_code_block():
    assert _should_use_card("```python\nprint('hi')\n```") is True


def test_should_use_card_detects_table():
    assert _should_use_card("| a | b |\n|---|---|\n| 1 | 2 |") is True


def test_should_use_card_plain_text():
    assert _should_use_card("just plain text") is False


def test_sent_message_ids_tracked_per_account():
    """After send(), the returned message_id is stored in _sent_message_ids for that account."""
    ch = make_channel()
    ch._clients["Operator"] = MagicMock()

    mock_response = MagicMock()
    mock_response.success.return_value = True
    mock_response.data.message_id = "om_sent123"
    ch._clients["Operator"].im.v1.message.create.return_value = mock_response

    msg = OutboundMessage(
        channel="feishu", chat_id="oc_group1", content="hello",
        metadata={"account_id": "Operator"},
    )
    asyncio.run(ch.send(msg))
    assert "om_sent123" in ch._sent_message_ids.get("Operator", set())


def _make_group_event(message_id, sender_open_id, sender_type, chat_id, content, mentions=None, account_id="Operator"):
    """Build a minimal mock P2ImMessageReceiveV1 event."""
    event = MagicMock()
    event.event.sender.sender_type = sender_type
    event.event.sender.sender_id.open_id = sender_open_id
    event.event.message.message_id = message_id
    event.event.message.chat_id = chat_id
    event.event.message.chat_type = "group"
    event.event.message.message_type = "text"
    event.event.message.content = '{"text": "' + content + '"}'
    event.event.message.mentions = mentions or []
    return event


@pytest.mark.asyncio
async def test_bot_message_skipped_if_self_sent():
    """Bot's own echoed message is ignored."""
    ch = make_channel()
    ch._loop = asyncio.get_event_loop()
    ch._bot_open_ids["Operator"] = "ou_operator"
    ch._record_sent("Operator", "om_self123")

    data = _make_group_event("om_self123", "ou_operator", "bot", "oc_g1", "hello")
    ch.agent_pool = MagicMock()

    await ch._on_message(data, "Operator")
    ch.agent_pool.get_agent.assert_not_called()


@pytest.mark.asyncio
async def test_bot_message_processed_when_mentioned():
    """Bot message from another bot is processed when this bot is @mentioned."""
    ch = make_channel()
    ch._loop = asyncio.get_event_loop()
    ch._bot_open_ids["Operator"] = "ou_operator"
    ch._bot_open_ids["VicePresident"] = "ou_vp"
    ch.config.require_mention = True

    mention = MagicMock()
    mention.key = "@_user_1"
    mention.id.open_id = "ou_operator"
    mention.name = "Operator"
    data = _make_group_event("om_other123", "ou_vp", "bot", "oc_g1", "@Operator hi", [mention])

    ch.agent_pool = MagicMock()
    ch.agent_pool.get_agent.return_value = MagicMock()
    ch.agent_pool.route_inbound = AsyncMock()
    ch._clients["Operator"] = MagicMock()
    ch._add_reaction = AsyncMock(return_value="rxn1")

    await ch._on_message(data, "Operator")
    ch.agent_pool.route_inbound.assert_called_once()


@pytest.mark.asyncio
async def test_bot_message_skipped_when_not_mentioned():
    """Bot message from another bot is ignored when this bot is not @mentioned."""
    ch = make_channel()
    ch._loop = asyncio.get_event_loop()
    ch._bot_open_ids["Operator"] = "ou_operator"
    ch._bot_open_ids["VicePresident"] = "ou_vp"
    ch.config.require_mention = True

    data = _make_group_event("om_other456", "ou_vp", "bot", "oc_g1", "just talking", [])

    ch.agent_pool = MagicMock()
    ch.agent_pool.get_agent.return_value = MagicMock()
    ch.agent_pool.route_inbound = AsyncMock()
    await ch._on_message(data, "Operator")
    ch.agent_pool.route_inbound.assert_not_called()


@pytest.mark.asyncio
async def test_group_message_from_bot_uses_account_name():
    """When a bot sends a group message, its account_id is used as sender name, not Contact API."""
    ch = make_channel()
    ch._bot_open_ids["VicePresident"] = "ou_vp"
    ch._clients["Operator"] = MagicMock()

    captured = []
    async def fake_route(msg):
        captured.append(msg)
    ch.agent_pool = MagicMock()
    ch.agent_pool.route_inbound = AsyncMock(side_effect=fake_route)

    await ch._handle_group_message(
        sender_id="ou_vp",
        chat_id="oc_g1",
        content="what do you think?",
        message_id="om_1",
        mentions=[],
        account_id="Operator",
        sender_type="bot",
    )
    assert captured[0].content.startswith("[VicePresident]:")


@pytest.mark.asyncio
async def test_reply_message_includes_quoted_content():
    """When parent_id is set, fetched parent content is prepended to message."""
    from nanobot.channels.feishu import FeishuChannel
    from nanobot.config.schema import FeishuConfig
    from nanobot.bus.queue import MessageBus
    from nanobot.bus.events import InboundMessage

    cfg = FeishuConfig(enabled=True, app_id="aid", app_secret="asec", allow_from=["ou_user1"])
    bus = MagicMock(spec=MessageBus)
    bus.publish_inbound = AsyncMock()
    ch = FeishuChannel(cfg, bus)

    # Mock lark client with im.v1.message.get returning parent content
    mock_client = MagicMock()
    mock_get_resp = MagicMock()
    mock_get_resp.success.return_value = True
    mock_get_resp.data = MagicMock()
    mock_get_resp.data.items = [MagicMock()]
    mock_get_resp.data.items[0].msg_type = "text"
    mock_get_resp.data.items[0].body = MagicMock()
    mock_get_resp.data.items[0].body.content = '{"text": "parent message text"}'
    mock_client.im.v1.message.get.return_value = mock_get_resp
    ch._clients["default"] = mock_client

    # Build a minimal P2ImMessageReceiveV1-like event with parent_id
    event_data = MagicMock()
    event_data.event.message.message_id = "msg_child"
    event_data.event.message.parent_id = "msg_parent"
    event_data.event.message.root_id = None
    event_data.event.message.chat_id = "ou_user1"
    event_data.event.message.chat_type = "p2p"
    event_data.event.message.message_type = "text"
    event_data.event.message.content = '{"text": "hello"}'
    event_data.event.message.mentions = []
    event_data.event.sender.sender_type = "user"
    event_data.event.sender.sender_id = MagicMock()
    event_data.event.sender.sender_id.open_id = "ou_user1"

    await ch._on_message(event_data, account_id="default")

    assert bus.publish_inbound.called
    published: InboundMessage = bus.publish_inbound.call_args[0][0]
    assert '[Replying to: "parent message text"]' in published.content
    assert "hello" in published.content


@pytest.mark.asyncio
async def test_group_reply_message_includes_quoted_content():
    """Quote injection also works for group messages."""
    from nanobot.channels.feishu import FeishuChannel
    from nanobot.config.schema import FeishuConfig
    from nanobot.bus.queue import MessageBus

    cfg = FeishuConfig(enabled=True, app_id="aid", app_secret="asec", require_mention=False)
    bus = MagicMock(spec=MessageBus)
    bus.publish_inbound = AsyncMock()
    ch = FeishuChannel(cfg, bus)

    mock_client = MagicMock()
    mock_get_resp = MagicMock()
    mock_get_resp.success.return_value = True
    mock_get_resp.data = MagicMock()
    mock_get_resp.data.items = [MagicMock()]
    mock_get_resp.data.items[0].msg_type = "text"
    mock_get_resp.data.items[0].body = MagicMock()
    mock_get_resp.data.items[0].body.content = '{"text": "original group msg"}'
    mock_client.im.v1.message.get.return_value = mock_get_resp
    ch._clients["default"] = mock_client

    event_data = MagicMock()
    event_data.event.message.message_id = "msg_group_child"
    event_data.event.message.parent_id = "msg_group_parent"
    event_data.event.message.root_id = None
    event_data.event.message.chat_id = "oc_group1"
    event_data.event.message.chat_type = "group"
    event_data.event.message.message_type = "text"
    event_data.event.message.content = '{"text": "reply text"}'
    event_data.event.message.mentions = []
    event_data.event.sender.sender_type = "user"
    event_data.event.sender.sender_id = MagicMock()
    event_data.event.sender.sender_id.open_id = "ou_user2"

    await ch._on_message(event_data, account_id="default")

    assert bus.publish_inbound.called
    from nanobot.bus.events import InboundMessage
    published: InboundMessage = bus.publish_inbound.call_args[0][0]
    assert '[Replying to: "original group msg"]' in published.content
