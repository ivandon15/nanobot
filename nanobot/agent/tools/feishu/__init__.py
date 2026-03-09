"""Feishu tools package."""
from nanobot.agent.tools.feishu.doc import FeishuDocTool
from nanobot.agent.tools.feishu.wiki import FeishuWikiTool
from nanobot.agent.tools.feishu.bitable import FeishuBitableTool
from nanobot.agent.tools.feishu.drive import FeishuDriveTool
from nanobot.agent.tools.feishu.task import FeishuTaskTool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.config.schema import FeishuConfig


def register_feishu_tools(registry: ToolRegistry, cfg: FeishuConfig, account_id: str | None = None) -> None:
    """Register enabled Feishu tools into the tool registry."""
    if cfg.tools.doc:
        registry.register(FeishuDocTool(cfg, account_id))
    if cfg.tools.wiki:
        registry.register(FeishuWikiTool(cfg, account_id))
    if cfg.tools.bitable:
        registry.register(FeishuBitableTool(cfg, account_id))
    if cfg.tools.drive:
        registry.register(FeishuDriveTool(cfg, account_id))
    if cfg.tools.task:
        registry.register(FeishuTaskTool(cfg, account_id))
