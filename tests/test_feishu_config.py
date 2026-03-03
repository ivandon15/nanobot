from nanobot.config.schema import FeishuConfig


def test_feishu_config_group_defaults():
    cfg = FeishuConfig()
    assert cfg.group_allow_from == []
    assert cfg.require_mention is True
    assert cfg.render_mode == "card"


def test_feishu_config_tools_defaults():
    cfg = FeishuConfig()
    assert cfg.tools.doc is True
    assert cfg.tools.wiki is True
    assert cfg.tools.bitable is True
    assert cfg.tools.drive is True
    assert cfg.tools.task is True


def test_feishu_config_camel_case_parsing():
    cfg = FeishuConfig.model_validate({
        "groupAllowFrom": ["oc_abc123"],
        "requireMention": False,
        "renderMode": "auto",
    })
    assert cfg.group_allow_from == ["oc_abc123"]
    assert cfg.require_mention is False
    assert cfg.render_mode == "auto"
