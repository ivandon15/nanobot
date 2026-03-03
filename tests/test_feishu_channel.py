from unittest.mock import AsyncMock, MagicMock, patch
from nanobot.channels.feishu import FeishuChannel


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

        result = ch._fetch_bot_open_id_sync(mock_client)
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

        result = ch._fetch_bot_open_id_sync(mock_client)
    assert result is None
