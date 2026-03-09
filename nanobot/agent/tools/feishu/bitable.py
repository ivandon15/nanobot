"""Feishu bitable tool (feishu_bitable)."""
import json
from typing import Any

from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.feishu.client import get_feishu_client
from nanobot.config.schema import FeishuConfig


class FeishuBitableTool(Tool):
    """CRUD operations on Feishu multi-dimensional tables (bitable)."""

    def __init__(self, cfg: FeishuConfig, account_id: str | None = None):
        self._cfg = cfg
        self._account_id = account_id

    @property
    def name(self) -> str:
        return "feishu_bitable"

    @property
    def description(self) -> str:
        return (
            "Feishu bitable (multi-dimensional table) operations. "
            "Actions: list_tables, list_fields, list_records, "
            "create_record, update_record, delete_record. "
            "app_token is the bitable app token from the URL."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list_tables", "list_fields", "list_records",
                             "create_record", "update_record", "delete_record"],
                    "description": "Operation to perform",
                },
                "app_token": {
                    "type": "string",
                    "description": "Bitable app token",
                },
                "table_id": {
                    "type": "string",
                    "description": "Table ID (required for most actions)",
                },
                "record_id": {
                    "type": "string",
                    "description": "Record ID (required for update/delete)",
                },
                "fields": {
                    "type": "object",
                    "description": "Field values for create/update",
                },
                "filter": {
                    "type": "string",
                    "description": "Filter expression for list_records",
                },
            },
            "required": ["action", "app_token"],
        }

    async def execute(self, action: str, app_token: str, table_id: str = "",
                      record_id: str = "", fields: dict | None = None,
                      filter: str = "", **kwargs: Any) -> str:
        import asyncio
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self._run, action, app_token, table_id, record_id, fields or {}, filter
        )

    def _run(self, action: str, app_token: str, table_id: str,
             record_id: str, fields: dict, filter_expr: str) -> str:
        from lark_oapi.api.bitable.v1.model import (
            ListAppTableRequest,
            ListAppTableFieldRequest,
            ListAppTableRecordRequest,
            CreateAppTableRecordRequest,
            UpdateAppTableRecordRequest,
            DeleteAppTableRecordRequest,
        )
        from lark_oapi.api.bitable.v1.model.app_table_record import AppTableRecord
        client = get_feishu_client(self._cfg, self._account_id)
        try:
            if action == "list_tables":
                req = ListAppTableRequest.builder().app_token(app_token).build()
                resp = client.bitable.v1.app_table.list(req)
                if not resp.success():
                    return f"Error: {resp.code} {resp.msg}"
                tables = [
                    {"table_id": t.table_id, "name": t.name}
                    for t in (resp.data.items or [])
                ]
                return json.dumps(tables, ensure_ascii=False)

            elif action == "list_fields":
                if not table_id:
                    return "Error: table_id required for list_fields"
                req = (
                    ListAppTableFieldRequest.builder()
                    .app_token(app_token)
                    .table_id(table_id)
                    .build()
                )
                resp = client.bitable.v1.app_table_field.list(req)
                if not resp.success():
                    return f"Error: {resp.code} {resp.msg}"
                fields_list = [
                    {"field_id": f.field_id, "field_name": f.field_name, "type": f.type}
                    for f in (resp.data.items or [])
                ]
                return json.dumps(fields_list, ensure_ascii=False)

            elif action == "list_records":
                if not table_id:
                    return "Error: table_id required for list_records"
                builder = ListAppTableRecordRequest.builder().app_token(app_token).table_id(table_id)
                if filter_expr:
                    builder = builder.filter(filter_expr)
                resp = client.bitable.v1.app_table_record.list(builder.build())
                if not resp.success():
                    return f"Error: {resp.code} {resp.msg}"
                records = [
                    {"record_id": r.record_id, "fields": r.fields}
                    for r in (resp.data.items or [])
                ]
                return json.dumps(records, ensure_ascii=False, default=str)

            elif action == "create_record":
                if not table_id:
                    return "Error: table_id required for create_record"
                body = AppTableRecord.builder().fields(fields).build()
                req = (
                    CreateAppTableRecordRequest.builder()
                    .app_token(app_token)
                    .table_id(table_id)
                    .request_body(body)
                    .build()
                )
                resp = client.bitable.v1.app_table_record.create(req)
                if not resp.success():
                    return f"Error: {resp.code} {resp.msg}"
                return json.dumps({"record_id": resp.data.record.record_id}, ensure_ascii=False)

            elif action == "update_record":
                if not table_id or not record_id:
                    return "Error: table_id and record_id required for update_record"
                body = AppTableRecord.builder().fields(fields).build()
                req = (
                    UpdateAppTableRecordRequest.builder()
                    .app_token(app_token)
                    .table_id(table_id)
                    .record_id(record_id)
                    .request_body(body)
                    .build()
                )
                resp = client.bitable.v1.app_table_record.update(req)
                if not resp.success():
                    return f"Error: {resp.code} {resp.msg}"
                return json.dumps({"record_id": resp.data.record.record_id}, ensure_ascii=False)

            elif action == "delete_record":
                if not table_id or not record_id:
                    return "Error: table_id and record_id required for delete_record"
                req = (
                    DeleteAppTableRecordRequest.builder()
                    .app_token(app_token)
                    .table_id(table_id)
                    .record_id(record_id)
                    .build()
                )
                resp = client.bitable.v1.app_table_record.delete(req)
                if not resp.success():
                    return f"Error: {resp.code} {resp.msg}"
                return json.dumps({"deleted": True, "record_id": record_id})

            else:
                return f"Error: unknown action '{action}'"
        except Exception as e:
            return f"Error: {e}"
