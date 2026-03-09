"""Feishu task tool (feishu_task)."""
import json
import time
from typing import Any

from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.feishu.client import get_feishu_client
from nanobot.config.schema import FeishuConfig


class FeishuTaskTool(Tool):
    """Manage Feishu tasks."""

    def __init__(self, cfg: FeishuConfig, account_id: str | None = None):
        self._cfg = cfg
        self._account_id = account_id

    @property
    def name(self) -> str:
        return "feishu_task"

    @property
    def description(self) -> str:
        return (
            "Feishu task management. "
            "Actions: list_tasks, create_task, complete_task, delete_task."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list_tasks", "create_task", "complete_task", "delete_task"],
                    "description": "Operation to perform",
                },
                "task_guid": {
                    "type": "string",
                    "description": "Task GUID (required for complete/delete)",
                },
                "summary": {
                    "type": "string",
                    "description": "Task title/summary (required for create_task)",
                },
                "due": {
                    "type": "string",
                    "description": "Due date as Unix timestamp string (optional for create_task)",
                },
                "tasklist_guid": {
                    "type": "string",
                    "description": "Tasklist GUID to filter by (optional for list_tasks)",
                },
            },
            "required": ["action"],
        }

    async def execute(self, action: str, task_guid: str = "", summary: str = "",
                      due: str = "", tasklist_guid: str = "", **kwargs: Any) -> str:
        import asyncio
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self._run, action, task_guid, summary, due, tasklist_guid
        )

    def _run(self, action: str, task_guid: str, summary: str, due: str, tasklist_guid: str) -> str:
        from lark_oapi.api.task.v2.model import (
            ListTaskRequest, CreateTaskRequest, PatchTaskRequest, DeleteTaskRequest,
        )
        from lark_oapi.api.task.v2.model.input_task import InputTask
        from lark_oapi.api.task.v2.model.due import Due
        from lark_oapi.api.task.v2.model.patch_task_request_body import PatchTaskRequestBody
        client = get_feishu_client(self._cfg, self._account_id)
        try:
            if action == "list_tasks":
                req = ListTaskRequest.builder().build()
                resp = client.task.v2.task.list(req)
                if not resp.success():
                    return f"Error: {resp.code} {resp.msg}"
                tasks = [
                    {
                        "guid": t.guid,
                        "summary": t.summary,
                        "completed": bool(t.completed_at),
                    }
                    for t in (resp.data.items or [])
                ]
                return json.dumps(tasks, ensure_ascii=False)

            elif action == "create_task":
                if not summary:
                    return "Error: summary required for create_task"
                task_builder = InputTask.builder().summary(summary)
                if due:
                    task_builder = task_builder.due(Due.builder().timestamp(due).build())
                req = CreateTaskRequest.builder().request_body(task_builder.build()).build()
                resp = client.task.v2.task.create(req)
                if not resp.success():
                    return f"Error: {resp.code} {resp.msg}"
                return json.dumps({"guid": resp.data.task.guid, "summary": resp.data.task.summary})

            elif action == "complete_task":
                if not task_guid:
                    return "Error: task_guid required for complete_task"
                completed_at = str(int(time.time() * 1000))
                task_body = InputTask.builder().completed_at(completed_at).build()
                body = PatchTaskRequestBody.builder().task(task_body).update_fields(["completed_at"]).build()
                req = PatchTaskRequest.builder().task_guid(task_guid).request_body(body).build()
                resp = client.task.v2.task.patch(req)
                if not resp.success():
                    return f"Error: {resp.code} {resp.msg}"
                return json.dumps({"guid": task_guid, "completed": True})

            elif action == "delete_task":
                if not task_guid:
                    return "Error: task_guid required for delete_task"
                req = DeleteTaskRequest.builder().task_guid(task_guid).build()
                resp = client.task.v2.task.delete(req)
                if not resp.success():
                    return f"Error: {resp.code} {resp.msg}"
                return json.dumps({"deleted": True, "guid": task_guid})

            else:
                return f"Error: unknown action '{action}'"
        except Exception as e:
            return f"Error: {e}"
