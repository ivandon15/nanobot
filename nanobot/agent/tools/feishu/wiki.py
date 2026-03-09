"""Feishu wiki tool (feishu_wiki)."""
import json
from typing import Any

from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.feishu.client import get_feishu_client
from nanobot.config.schema import FeishuConfig


class FeishuWikiTool(Tool):
    """Browse Feishu wiki spaces and nodes."""

    def __init__(self, cfg: FeishuConfig, account_id: str | None = None):
        self._cfg = cfg
        self._account_id = account_id

    @property
    def name(self) -> str:
        return "feishu_wiki"

    @property
    def description(self) -> str:
        return (
            "Feishu wiki operations. "
            "Actions: list_spaces (list all wiki spaces), "
            "list_nodes (list nodes in a space), "
            "get_node (get a specific node by token)."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list_spaces", "list_nodes", "get_node"],
                    "description": "Operation to perform",
                },
                "space_id": {
                    "type": "string",
                    "description": "Wiki space ID (required for list_nodes)",
                },
                "node_token": {
                    "type": "string",
                    "description": "Node token (required for get_node)",
                },
                "parent_node_token": {
                    "type": "string",
                    "description": "Parent node token to list children (optional for list_nodes)",
                },
            },
            "required": ["action"],
        }

    async def execute(self, action: str, space_id: str = "", node_token: str = "",
                      parent_node_token: str = "", **kwargs: Any) -> str:
        import asyncio
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._run, action, space_id, node_token, parent_node_token)

    def _run(self, action: str, space_id: str, node_token: str, parent_node_token: str) -> str:
        from lark_oapi.api.wiki.v2.model import (
            ListSpaceRequest, ListSpaceNodeRequest, GetNodeSpaceRequest,
        )
        client = get_feishu_client(self._cfg, self._account_id)
        try:
            if action == "list_spaces":
                req = ListSpaceRequest.builder().build()
                resp = client.wiki.v2.space.list(req)
                if not resp.success():
                    return f"Error: {resp.code} {resp.msg}"
                spaces = [
                    {"space_id": s.space_id, "name": s.name}
                    for s in (resp.data.items or [])
                ]
                return json.dumps(spaces, ensure_ascii=False)

            elif action == "list_nodes":
                if not space_id:
                    return "Error: space_id required for list_nodes"
                builder = ListSpaceNodeRequest.builder().space_id(space_id)
                if parent_node_token:
                    builder = builder.parent_node_token(parent_node_token)
                resp = client.wiki.v2.space_node.list(builder.build())
                if not resp.success():
                    return f"Error: {resp.code} {resp.msg}"
                nodes = [
                    {"node_token": n.node_token, "title": n.title, "obj_type": n.obj_type}
                    for n in (resp.data.items or [])
                ]
                return json.dumps(nodes, ensure_ascii=False)

            elif action == "get_node":
                if not node_token:
                    return "Error: node_token required for get_node"
                req = GetNodeSpaceRequest.builder().token(node_token).build()
                resp = client.wiki.v2.space.get_node(req)
                if not resp.success():
                    return f"Error: {resp.code} {resp.msg}"
                node = resp.data.node
                return json.dumps({
                    "node_token": node.node_token,
                    "title": node.title,
                    "obj_type": node.obj_type,
                    "obj_token": node.obj_token,
                }, ensure_ascii=False)

            else:
                return f"Error: unknown action '{action}'"
        except Exception as e:
            return f"Error: {e}"
