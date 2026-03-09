"""Feishu document tool (feishu_doc)."""
import json
import time
from typing import Any

from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.feishu.client import get_feishu_client
from nanobot.config.schema import FeishuConfig

_MAX_BLOCKS_PER_INSERT = 50
_RETRYABLE_CODES = {429, 1254290, 1254291, 1255040}


class FeishuDocTool(Tool):
    """Read and write Feishu documents (docx)."""

    def __init__(self, cfg: FeishuConfig, account_id: str | None = None):
        self._cfg = cfg
        self._account_id = account_id

    @property
    def name(self) -> str:
        return "feishu_doc"

    @property
    def description(self) -> str:
        return (
            "Feishu document operations. "
            "Actions: read (get full text), create (create blank doc), "
            "create_and_write (create + write markdown content). "
            "doc_id is the document token from the URL."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["read", "create", "create_and_write"],
                    "description": "Operation to perform",
                },
                "doc_id": {
                    "type": "string",
                    "description": "Document token (required for read)",
                },
                "title": {
                    "type": "string",
                    "description": "Document title (for create/create_and_write)",
                },
                "content": {
                    "type": "string",
                    "description": "Markdown content to write (for create_and_write)",
                },
                "folder_token": {
                    "type": "string",
                    "description": "Parent folder token (optional, for create)",
                },
            },
            "required": ["action"],
        }

    async def execute(self, action: str, doc_id: str = "", title: str = "",
                      content: str = "", folder_token: str = "", **kwargs: Any) -> str:
        import asyncio
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._run, action, doc_id, title, content, folder_token)

    def _convert_markdown(self, client: Any, markdown: str) -> tuple[list, list]:
        from lark_oapi.api.docx.v1.model import ConvertDocumentRequest, ConvertDocumentRequestBody
        body = ConvertDocumentRequestBody.builder().content_type("markdown").content(markdown).build()
        req = ConvertDocumentRequest.builder().request_body(body).build()
        resp = client.docx.v1.document.convert(req)
        if not resp.success():
            raise RuntimeError(f"markdown convert failed: {resp.code} {resp.msg}")
        return resp.data.blocks or [], resp.data.first_level_block_ids or []

    def _insert_blocks_batch(self, client: Any, doc_id: str, blocks: list) -> list:
        from lark_oapi.api.docx.v1.model import (
            CreateDocumentBlockChildrenRequest,
            CreateDocumentBlockChildrenRequestBody,
        )
        inserted = []
        for i in range(0, len(blocks), _MAX_BLOCKS_PER_INSERT):
            batch = self._clean_blocks(blocks[i:i + _MAX_BLOCKS_PER_INSERT])
            if not batch:
                continue
            for attempt in range(4):
                body = CreateDocumentBlockChildrenRequestBody.builder().children(batch).build()
                req = (
                    CreateDocumentBlockChildrenRequest.builder()
                    .document_id(doc_id)
                    .block_id(doc_id)
                    .request_body(body)
                    .build()
                )
                resp = client.docx.v1.document_block_children.create(req)
                if resp.success():
                    inserted.extend(resp.data.children or [])
                    break
                if resp.code in _RETRYABLE_CODES and attempt < 3:
                    time.sleep(0.25 * (2 ** attempt))
                    continue
                raise RuntimeError(f"insert blocks failed: {resp.code} {resp.msg}")
        return inserted

    @staticmethod
    def _clean_blocks(blocks: list) -> list:
        cleaned = []
        for b in blocks:
            c = {k: v for k, v in b.__dict__.items() if v is not None and not k.startswith("_")}
            c.pop("block_id", None)
            c.pop("parent_id", None)
            c.pop("children", None)
            cleaned.append(c)
        return cleaned

    def _reorder_blocks(self, blocks: list, first_ids: list) -> list:
        if not first_ids:
            return blocks
        id_map = {b.block_id: b for b in blocks if hasattr(b, "block_id") and b.block_id}
        ordered = [id_map[fid] for fid in first_ids if fid in id_map]
        return ordered if ordered else blocks

    def _process_images(self, client: Any, doc_id: str, markdown: str, inserted: list) -> int:
        """Upload images from markdown URLs into the corresponding image blocks."""
        import re
        import urllib.request
        from lark_oapi.api.drive.v1.model import UploadAllMediaRequest, UploadAllMediaRequestBody
        from lark_oapi.api.docx.v1.model.patch_document_block_request import PatchDocumentBlockRequest
        from lark_oapi.api.docx.v1.model.update_block_request import UpdateBlockRequest
        from lark_oapi.api.docx.v1.model.replace_image_request import ReplaceImageRequest

        urls = re.findall(r'!\[[^\]]*\]\((https?://[^)]+)\)', markdown)
        if not urls:
            return 0

        image_blocks = [b for b in inserted if getattr(b, "block_type", None) == 27]
        processed = 0

        for i, url in enumerate(urls[:len(image_blocks)]):
            block_id = image_blocks[i].block_id
            try:
                with urllib.request.urlopen(url, timeout=30) as resp:
                    data = resp.read()
                file_name = url.split("/")[-1].split("?")[0] or f"image_{i}.png"
                if "." not in file_name:
                    file_name += ".png"

                import io
                upload_body = (
                    UploadAllMediaRequestBody.builder()
                    .file_name(file_name)
                    .parent_type("docx_image")
                    .parent_node(block_id)
                    .size(len(data))
                    .file(io.BytesIO(data))
                    .build()
                )
                upload_req = UploadAllMediaRequest.builder().request_body(upload_body).build()
                upload_resp = client.drive.v1.media.upload_all(upload_req)
                if not upload_resp.success():
                    continue
                file_token = upload_resp.data.file_token

                replace = ReplaceImageRequest.builder().token(file_token).build()
                update = UpdateBlockRequest.builder().replace_image(replace).build()
                patch_req = (
                    PatchDocumentBlockRequest.builder()
                    .document_id(doc_id)
                    .block_id(block_id)
                    .request_body(update)
                    .build()
                )
                patch_resp = client.docx.v1.document_block.patch(patch_req)
                if patch_resp.success():
                    processed += 1
            except Exception:
                continue

        return processed

    def _run(self, action: str, doc_id: str, title: str, content: str, folder_token: str) -> str:
        from lark_oapi.api.docx.v1.model import (
            CreateDocumentRequest, CreateDocumentRequestBody,
            RawContentDocumentRequest,
        )
        client = get_feishu_client(self._cfg, self._account_id)
        try:
            if action == "read":
                if not doc_id:
                    return "Error: doc_id required for read"
                req = RawContentDocumentRequest.builder().document_id(doc_id).lang(0).build()
                resp = client.docx.v1.document.raw_content(req)
                if not resp.success():
                    return f"Error: {resp.code} {resp.msg}"
                return resp.data.content or "(empty document)"

            elif action == "create":
                body_builder = CreateDocumentRequestBody.builder().title(title or "Untitled")
                if folder_token:
                    body_builder = body_builder.folder_token(folder_token)
                req = CreateDocumentRequest.builder().request_body(body_builder.build()).build()
                resp = client.docx.v1.document.create(req)
                if not resp.success():
                    return f"Error: {resp.code} {resp.msg}"
                new_id = resp.data.document.document_id
                return json.dumps({"doc_id": new_id, "url": f"https://feishu.cn/docx/{new_id}"})

            elif action == "create_and_write":
                body_builder = CreateDocumentRequestBody.builder().title(title or "Untitled")
                if folder_token:
                    body_builder = body_builder.folder_token(folder_token)
                req = CreateDocumentRequest.builder().request_body(body_builder.build()).build()
                resp = client.docx.v1.document.create(req)
                if not resp.success():
                    return f"Error creating doc: {resp.code} {resp.msg}"
                new_id = resp.data.document.document_id
                if not content:
                    return json.dumps({"doc_id": new_id, "url": f"https://feishu.cn/docx/{new_id}"})
                blocks, first_ids = self._convert_markdown(client, content)
                ordered = self._reorder_blocks(blocks, first_ids)
                inserted = self._insert_blocks_batch(client, new_id, ordered)
                images_processed = self._process_images(client, new_id, content, inserted)
                return json.dumps({
                    "doc_id": new_id,
                    "url": f"https://feishu.cn/docx/{new_id}",
                    "blocks_added": len(inserted),
                    "images_processed": images_processed,
                })

            else:
                return f"Error: unknown action '{action}'"
        except Exception as e:
            return f"Error: {e}"
