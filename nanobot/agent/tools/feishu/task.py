"""Feishu task tool (feishu_task)."""
import json
from typing import Any

from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.feishu.client import get_feishu_client
from nanobot.config.schema import FeishuConfig


class FeishuTaskTool(Tool):
    """Manage Feishu tasks."""

    def __init__(self, cfg: FeishuConfig):
        self._cfg = cfg

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
        client = get_feishu_client(self._cfg)
        try:
            if action == "list_tasks":
                kwargs: dict[str, Any] = {}
                if tasklist_guid:
                    kwargs["tasklist_guid"] = tasklist_guid
                resp = client.task.v2.task.list(**kwargs)
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
                body: dict[str, Any] = {"summary": summary}
                if due:
                    body["due"] = {"timestamp": due}
                resp = client.task.v2.task.create(request_body=body)
                if not resp.success():
                    return f"Error: {resp.code} {resp.msg}"
                return json.dumps({"guid": resp.data.task.guid, "summary": resp.data.task.summary})

            elif action == "complete_task":
                if not task_guid:
                    return "Error: task_guid required for complete_task"
                import time
                resp = client.task.v2.task.patch(
                    task_guid=task_guid,
                    request_body={"task": {"completed_at": str(int(time.time() * 1000))},
                                  "update_fields": ["completed_at"]},
                )
                if not resp.success():
                    return f"Error: {resp.code} {resp.msg}"
                return json.dumps({"guid": task_guid, "completed": True})

            elif action == "delete_task":
                if not task_guid:
                    return "Error: task_guid required for delete_task"
                resp = client.task.v2.task.delete(task_guid=task_guid)
                if not resp.success():
                    return f"Error: {resp.code} {resp.msg}"
                return json.dumps({"deleted": True, "guid": task_guid})

            else:
                return f"Error: unknown action '{action}'"
        except Exception as e:
            return f"Error: {e}"
