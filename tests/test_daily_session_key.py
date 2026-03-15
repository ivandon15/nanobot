from datetime import date
from nanobot.bus.events import InboundMessage


def test_session_key_includes_date():
    msg = InboundMessage(channel="feishu", sender_id="u1", chat_id="c1", content="hi")
    today = date.today().isoformat()
    assert msg.session_key == f"feishu:c1:{today}"


def test_session_key_override_still_works():
    msg = InboundMessage(channel="feishu", sender_id="u1", chat_id="c1", content="hi",
                         session_key_override="custom:key")
    assert msg.session_key == "custom:key"
