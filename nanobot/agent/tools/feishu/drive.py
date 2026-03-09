"""Feishu drive tool (feishu_drive)."""
import json
from typing import Any

from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.feishu.client import get_feishu_client
from nanobot.config.schema import FeishuConfig


class FeishuDriveTool(Tool):
    """Browse and manage Feishu cloud drive files."""

    def __init__(self, cfg: FeishuConfig, account_id: str | None = None):
        self._cfg = cfg
        self._account_id = account_id

    @property
    def name(self) -> str:
        return "feishu_drive"

    @property
    def description(self) -> str:
        return (
            "Feishu drive operations. "
            "Actions: list_files (list files in a folder), "
            "create_folder (create a new folder). "
            "folder_token is the folder token from the URL (empty = root)."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list_files", "create_folder"],
                    "description": "Operation to perform",
                },
                "folder_token": {
                    "type": "string",
                    "description": "Folder token (empty = root folder)",
                },
                "name": {
                    "type": "string",
                    "description": "Folder name (required for create_folder)",
                },
            },
            "required": ["action"],
        }

    async def execute(self, action: str, folder_token: str = "",
                      name: str = "", **kwargs: Any) -> str:
        import asyncio
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._run, action, folder_token, name)

    def _run(self, action: str, folder_token: str, name: str) -> str:
        from lark_oapi.api.drive.v1.model import (
            ListFileRequest, CreateFolderFileRequest, CreateFolderFileRequestBody,
        )
        client = get_feishu_client(self._cfg, self._account_id)
        try:
            if action == "list_files":
                builder = ListFileRequest.builder()
                if folder_token:
                    builder = builder.folder_token(folder_token)
                resp = client.drive.v1.file.list(builder.build())
                if not resp.success():
                    return f"Error: {resp.code} {resp.msg}"
                files = [
                    {"token": f.token, "name": f.name, "type": f.type}
                    for f in (resp.data.files or [])
                ]
                return json.dumps(files, ensure_ascii=False)

            elif action == "create_folder":
                if not name:
                    return "Error: name required for create_folder"
                body_builder = CreateFolderFileRequestBody.builder().name(name)
                if folder_token:
                    body_builder = body_builder.folder_token(folder_token)
                req = CreateFolderFileRequest.builder().request_body(body_builder.build()).build()
                resp = client.drive.v1.file.create_folder(req)
                if not resp.success():
                    return f"Error: {resp.code} {resp.msg}"
                return json.dumps({
                    "token": resp.data.token,
                    "url": resp.data.url,
                }, ensure_ascii=False)

            else:
                return f"Error: unknown action '{action}'"
        except Exception as e:
            return f"Error: {e}"
