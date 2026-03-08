"""Feishu/Lark channel implementation using lark-oapi SDK with WebSocket long connection."""

import asyncio
import json
import os
import re
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

from loguru import logger

try:
    import requests as _requests
except ImportError:
    _requests = None  # type: ignore

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.config.schema import FeishuConfig

try:
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import (
        CreateFileRequest,
        CreateFileRequestBody,
        CreateImageRequest,
        CreateImageRequestBody,
        CreateMessageReactionRequest,
        CreateMessageReactionRequestBody,
        CreateMessageRequest,
        CreateMessageRequestBody,
        DeleteMessageReactionRequest,
        Emoji,
        GetMessageRequest,
        GetMessageResourceRequest,
        P2ImMessageReceiveV1,
    )
    FEISHU_AVAILABLE = True
except ImportError:
    FEISHU_AVAILABLE = False
    lark = None
    Emoji = None

# Message type display mapping
MSG_TYPE_MAP = {
    "image": "[image]",
    "audio": "[audio]",
    "file": "[file]",
    "sticker": "[sticker]",
}


def _extract_share_card_content(content_json: dict, msg_type: str) -> str:
    """Extract text representation from share cards and interactive messages."""
    parts = []

    if msg_type == "share_chat":
        parts.append(f"[shared chat: {content_json.get('chat_id', '')}]")
    elif msg_type == "share_user":
        parts.append(f"[shared user: {content_json.get('user_id', '')}]")
    elif msg_type == "interactive":
        parts.extend(_extract_interactive_content(content_json))
    elif msg_type == "share_calendar_event":
        parts.append(f"[shared calendar event: {content_json.get('event_key', '')}]")
    elif msg_type == "system":
        parts.append("[system message]")
    elif msg_type == "merge_forward":
        parts.append("[merged forward messages]")

    return "\n".join(parts) if parts else f"[{msg_type}]"


def _extract_interactive_content(content: dict) -> list[str]:
    """Recursively extract text and links from interactive card content."""
    parts = []

    if isinstance(content, str):
        try:
            content = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return [content] if content.strip() else []

    if not isinstance(content, dict):
        return parts

    if "title" in content:
        title = content["title"]
        if isinstance(title, dict):
            title_content = title.get("content", "") or title.get("text", "")
            if title_content:
                parts.append(f"title: {title_content}")
        elif isinstance(title, str):
            parts.append(f"title: {title}")

    for elements in content.get("elements", []) if isinstance(content.get("elements"), list) else []:
        for element in elements:
            parts.extend(_extract_element_content(element))

    card = content.get("card", {})
    if card:
        parts.extend(_extract_interactive_content(card))

    header = content.get("header", {})
    if header:
        header_title = header.get("title", {})
        if isinstance(header_title, dict):
            header_text = header_title.get("content", "") or header_title.get("text", "")
            if header_text:
                parts.append(f"title: {header_text}")

    return parts


def _extract_element_content(element: dict) -> list[str]:
    """Extract content from a single card element."""
    parts = []

    if not isinstance(element, dict):
        return parts

    tag = element.get("tag", "")

    if tag in ("markdown", "lark_md"):
        content = element.get("content", "")
        if content:
            parts.append(content)

    elif tag == "div":
        text = element.get("text", {})
        if isinstance(text, dict):
            text_content = text.get("content", "") or text.get("text", "")
            if text_content:
                parts.append(text_content)
        elif isinstance(text, str):
            parts.append(text)
        for field in element.get("fields", []):
            if isinstance(field, dict):
                field_text = field.get("text", {})
                if isinstance(field_text, dict):
                    c = field_text.get("content", "")
                    if c:
                        parts.append(c)

    elif tag == "a":
        href = element.get("href", "")
        text = element.get("text", "")
        if href:
            parts.append(f"link: {href}")
        if text:
            parts.append(text)

    elif tag == "button":
        text = element.get("text", {})
        if isinstance(text, dict):
            c = text.get("content", "")
            if c:
                parts.append(c)
        url = element.get("url", "") or element.get("multi_url", {}).get("url", "")
        if url:
            parts.append(f"link: {url}")

    elif tag == "img":
        alt = element.get("alt", {})
        parts.append(alt.get("content", "[image]") if isinstance(alt, dict) else "[image]")

    elif tag == "note":
        for ne in element.get("elements", []):
            parts.extend(_extract_element_content(ne))

    elif tag == "column_set":
        for col in element.get("columns", []):
            for ce in col.get("elements", []):
                parts.extend(_extract_element_content(ce))

    elif tag == "plain_text":
        content = element.get("content", "")
        if content:
            parts.append(content)

    else:
        for ne in element.get("elements", []):
            parts.extend(_extract_element_content(ne))

    return parts


def _extract_post_content(content_json: dict) -> tuple[str, list[str]]:
    """Extract text and image keys from Feishu post (rich text) message.

    Handles three payload shapes:
    - Direct:    {"title": "...", "content": [[...]]}
    - Localized: {"zh_cn": {"title": "...", "content": [...]}}
    - Wrapped:   {"post": {"zh_cn": {"title": "...", "content": [...]}}}
    """

    def _parse_block(block: dict) -> tuple[str | None, list[str]]:
        if not isinstance(block, dict) or not isinstance(block.get("content"), list):
            return None, []
        texts, images = [], []
        if title := block.get("title"):
            texts.append(title)
        for row in block["content"]:
            if not isinstance(row, list):
                continue
            for el in row:
                if not isinstance(el, dict):
                    continue
                tag = el.get("tag")
                if tag in ("text", "a"):
                    texts.append(el.get("text", ""))
                elif tag == "at":
                    texts.append(f"@{el.get('user_name', 'user')}")
                elif tag == "img" and (key := el.get("image_key")):
                    images.append(key)
                elif tag == "quote":
                    # Recursively extract text from the nested quote block
                    inner_content = el.get("content", [])
                    inner_texts = []
                    for inner_row in inner_content:
                        if not isinstance(inner_row, list):
                            continue
                        for inner_el in inner_row:
                            if not isinstance(inner_el, dict):
                                continue
                            inner_tag = inner_el.get("tag")
                            if inner_tag in ("text", "a"):
                                inner_texts.append(inner_el.get("text", ""))
                            elif inner_tag == "at":
                                inner_texts.append(f"@{inner_el.get('user_name', 'user')}")
                    if inner_texts:
                        quoted_text = " ".join(inner_texts).strip()
                        texts.append(f"[Quoted: {quoted_text}]")
        return (" ".join(texts).strip() or None), images

    # Unwrap optional {"post": ...} envelope
    root = content_json
    if isinstance(root, dict) and isinstance(root.get("post"), dict):
        root = root["post"]
    if not isinstance(root, dict):
        return "", []

    # Direct format
    if "content" in root:
        text, imgs = _parse_block(root)
        if text or imgs:
            return text or "", imgs

    # Localized: prefer known locales, then fall back to any dict child
    for key in ("zh_cn", "en_us", "ja_jp"):
        if key in root:
            text, imgs = _parse_block(root[key])
            if text or imgs:
                return text or "", imgs
    for val in root.values():
        if isinstance(val, dict):
            text, imgs = _parse_block(val)
            if text or imgs:
                return text or "", imgs

    return "", []



class _FixedWsClient(lark.ws.Client if FEISHU_AVAILABLE else object):
    """lark.ws.Client subclass that fixes multi-account use.

    The upstream client stores a module-level ``loop`` variable and calls
    ``loop.create_task(...)`` inside ``_connect`` and ``_receive_message_loop``.
    When two threads each run ``asyncio.run()``, they get different loops but
    both write to the same module-level variable, causing
    "Future attached to a different loop" errors.

    This subclass overrides those two methods to use
    ``asyncio.get_running_loop()`` instead.
    """

    async def _connect(self) -> None:
        import websockets as _ws
        from urllib.parse import urlparse, parse_qs
        import lark_oapi.ws.client as _lark_ws_mod
        from lark_oapi.ws.const import DEVICE_ID, SERVICE_ID
        from lark_oapi.core.log import logger as _lark_logger

        _loop = asyncio.get_running_loop()
        await self._lock.acquire()
        if self._conn is not None:
            return
        try:
            conn_url = self._get_conn_url()
            u = urlparse(conn_url)
            q = parse_qs(u.query)
            conn_id = q[DEVICE_ID][0]
            service_id = q[SERVICE_ID][0]

            conn = await _ws.connect(conn_url)
            self._conn = conn
            self._conn_url = conn_url
            self._conn_id = conn_id
            self._service_id = service_id

            _lark_logger.info(self._fmt_log("connected to {}", conn_url))
            _loop.create_task(self._receive_message_loop())
        except _ws.InvalidStatusCode as e:
            _lark_ws_mod._parse_ws_conn_exception(e)
        finally:
            self._lock.release()

    async def _receive_message_loop(self):
        from lark_oapi.ws.exception import ConnectionClosedException
        from lark_oapi.core.log import logger as _lark_logger

        _loop = asyncio.get_running_loop()
        try:
            while True:
                if self._conn is None:
                    raise ConnectionClosedException("connection is closed")
                msg = await self._conn.recv()
                _loop.create_task(self._handle_message(msg))
        except Exception as e:
            _lark_logger.error(self._fmt_log("receive message loop exit, err: {}", e))
            await self._disconnect()
            if self._auto_reconnect:
                await self._reconnect()
            else:
                raise e


def _should_use_card(text: str) -> bool:
    """Detect if text contains markdown that benefits from card rendering."""
    return bool(
        re.search(r"```[\s\S]*?```", text) or          # code blocks
        re.search(r"\|.+\|[\r\n]+\|[-:| ]+\|", text)  # tables
    )


def _check_bot_mentioned(mentions: list[dict], bot_open_id: str | None) -> bool:
    """Check if bot is @mentioned in a message."""
    if not bot_open_id:
        return False
    return any(m.get("id", {}).get("open_id") == bot_open_id for m in mentions)


def _strip_bot_mention(text: str, mentions: list[dict]) -> str:
    """Remove @BotName tokens from message text."""
    result = text
    for m in mentions:
        name = m.get("name", "")
        key = m.get("key", "")
        if name:
            result = re.sub(rf"@{re.escape(name)}\s*", "", result)
        if key:
            result = result.replace(key, "")
    return result.strip()


class FeishuChannel(BaseChannel):
    """
    Feishu/Lark channel using WebSocket long connection.

    Uses WebSocket to receive events - no public IP or webhook required.

    Requires:
    - App ID and App Secret from Feishu Open Platform
    - Bot capability enabled
    - Event subscription enabled (im.message.receive_v1)
    """

    name = "feishu"

    def __init__(self, config: FeishuConfig, bus: MessageBus, agent_pool: Any = None):
        super().__init__(config, bus)
        self.config: FeishuConfig = config
        self.agent_pool = agent_pool
        self._client: Any = None
        self._ws_client: Any = None
        self._ws_thread: threading.Thread | None = None
        self._clients: dict[str, Any] = {}
        self._ws_clients: dict[str, Any] = {}
        self._ws_threads: dict[str, threading.Thread] = {}
        self._app_id_to_account: dict[str, str] = {}
        self._processed_message_ids: OrderedDict[str, None] = OrderedDict()  # Ordered dedup cache
        self._loop: asyncio.AbstractEventLoop | None = None
        self._bot_open_ids: dict[str, str] = {}  # account_id -> bot open_id
        self._sent_message_ids: dict[str, OrderedDict[str, None]] = {}  # account_id -> sent msg_ids

    def _get_accounts(self) -> dict[str, dict[str, str]]:
        """Get accounts dict with backward compatibility.

        Returns dict[accountId, {app_id, app_secret}].
        If config.accounts is empty, use legacy appId/appSecret as "default" account.
        """
        if self.config.accounts:
            return {
                account_id: {
                    "app_id": account.app_id,
                    "app_secret": account.app_secret,
                }
                for account_id, account in self.config.accounts.items()
            }
        return {
            "default": {
                "app_id": self.config.app_id,
                "app_secret": self.config.app_secret,
            }
        }

    def _identify_account(self, app_id: str) -> str | None:
        """Map appId to accountId using the internal mapping."""
        return self._app_id_to_account.get(app_id)

    async def start(self) -> None:
        """Start the Feishu bot with WebSocket long connection."""
        if not FEISHU_AVAILABLE:
            logger.error("Feishu SDK not installed. Run: pip install lark-oapi")
            return

        accounts = self._get_accounts()
        if not accounts:
            logger.error("No Feishu accounts configured")
            return

        self._running = True
        self._loop = asyncio.get_running_loop()

        # Create clients and WebSocket connections for each account
        for account_id, account_info in accounts.items():
            app_id = account_info["app_id"]
            app_secret = account_info["app_secret"]

            # Create Lark client for sending messages
            client = lark.Client.builder() \
                .app_id(app_id) \
                .app_secret(app_secret) \
                .log_level(lark.LogLevel.INFO) \
                .build()
            self._clients[account_id] = client

            # Fetch bot open_id for mention detection
            loop = asyncio.get_running_loop()
            open_id = await loop.run_in_executor(None, self._fetch_bot_open_id_sync, app_id, app_secret)
            if open_id:
                self._bot_open_ids[account_id] = open_id
                logger.info("Feishu bot open_id for {}: {}", account_id, open_id)

            # Create event handler
            event_handler = lark.EventDispatcherHandler.builder(
                self.config.encrypt_key or "",
                self.config.verification_token or "",
            ).register_p2_im_message_receive_v1(
                lambda data, aid=account_id: self._on_message_sync(data, aid)
            ).build()

            # Each account runs in its own daemon thread with an isolated asyncio.run() loop.
            # _FixedWsClient overrides _connect/_receive_message_loop to use
            # asyncio.get_running_loop() instead of the module-level loop variable,
            # which is what caused "Future attached to a different loop" with multiple accounts.
            def run_ws(aid=account_id, _app_id=app_id, _app_secret=app_secret, _eh=event_handler):
                async def _run_forever():
                    ws_client = _FixedWsClient(
                        _app_id, _app_secret,
                        event_handler=_eh,
                        log_level=lark.LogLevel.INFO,
                        auto_reconnect=False,
                    )
                    self._ws_clients[aid] = ws_client
                    ping_task = None
                    retry_delay = 5
                    while self._running:
                        try:
                            await ws_client._connect()
                            retry_delay = 5  # reset on successful connect
                            if ping_task:
                                ping_task.cancel()
                            ping_task = asyncio.get_running_loop().create_task(ws_client._ping_loop())
                            while self._running and ws_client._conn is not None:
                                await asyncio.sleep(1)
                        except Exception as e:
                            if e:
                                logger.warning("Feishu WebSocket error ({}): {}", aid, e)
                            retry_delay = min(retry_delay * 2, 60)
                        if self._running:
                            await asyncio.sleep(retry_delay)

                asyncio.run(_run_forever())

            ws_thread = threading.Thread(target=run_ws, daemon=True)
            ws_thread.start()
            self._ws_threads[account_id] = ws_thread

            # Build reverse mapping
            self._app_id_to_account[app_id] = account_id

        logger.info("Feishu bot started with {} account(s)", len(accounts))

        # Keep running until stopped
        while self._running:
            await asyncio.sleep(1)

    async def stop(self) -> None:
        """
        Stop the Feishu bot.

        Notice: lark.ws.Client does not expose stop method， simply exiting the program will close the client.

        Reference: https://github.com/larksuite/oapi-sdk-python/blob/v2_main/lark_oapi/ws/client.py#L86
        """
        self._running = False
        logger.info("Feishu bot stopped")

    def _fetch_bot_open_id_sync(self, app_id: str, app_secret: str) -> str | None:
        """Fetch this bot's own open_id via /open-apis/bot/v3/info."""
        try:
            token_resp = _requests.post(
                "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": app_id, "app_secret": app_secret},
                timeout=10,
            )
            token_data = token_resp.json()
            token = token_data.get("tenant_access_token")
            if not token:
                return None
            bot_resp = _requests.get(
                "https://open.feishu.cn/open-apis/bot/v3/info",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            )
            data = bot_resp.json()
            if data.get("code") == 0:
                return data.get("bot", {}).get("open_id")
            return None
        except Exception as e:
            logger.warning("Failed to fetch bot open_id: {}", e)
            return None

    _sender_name_cache: dict[str, tuple[str, float]] = {}
    _SENDER_NAME_TTL = 600.0  # 10 minutes

    def _fetch_sender_name_sync(self, open_id: str, client: Any) -> str | None:
        """Fetch display name for a user open_id (cached)."""
        import time
        cached = self._sender_name_cache.get(open_id)
        if cached and cached[1] > time.time():
            return cached[0]
        try:
            from lark_oapi.api.contact.v3 import GetUserRequest
            request = GetUserRequest.builder() \
                .user_id(open_id) \
                .user_id_type("open_id") \
                .build()
            res = client.contact.v3.user.get(request)
            if res.success() and res.data and res.data.user:
                name = res.data.user.name or getattr(res.data.user, "nickname", None)
                if name:
                    self._sender_name_cache[open_id] = (name, time.time() + self._SENDER_NAME_TTL)
                    return name
        except Exception as e:
            logger.debug("Failed to fetch sender name for {}: {}", open_id, e)
        return None

    def _fetch_message_content_sync(self, message_id: str, client) -> str | None:
        """Fetch a message by ID and return its plain-text content. Best-effort."""
        try:
            request = GetMessageRequest.builder().message_id(message_id).build()
            response = client.im.v1.message.get(request)
            if not response.success():
                return None
            items = getattr(response.data, "items", None) or []
            if not items:
                return None
            item = items[0]
            msg_type = getattr(item, "msg_type", "text") or "text"
            body = getattr(item, "body", None)
            raw = getattr(body, "content", "") or ""
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                return raw.strip() or None
            if msg_type == "text":
                return parsed.get("text", "").strip() or None
            if msg_type == "post":
                text, _ = _extract_post_content(parsed)
                return text.strip() or None
            # For other types return a placeholder
            return MSG_TYPE_MAP.get(msg_type, f"[{msg_type}]")
        except Exception as e:
            logger.debug("Failed to fetch message {}: {}", message_id, e)
            return None

    async def _handle_group_message(
        self,
        sender_id: str,
        chat_id: str,
        content: str,
        message_id: str,
        mentions: list[dict],
        account_id: str,
        media: list[str] | None = None,
        metadata: dict | None = None,
        sender_type: str = "user",
    ) -> None:
        """Handle group message: strip bot mention, inject sender name, forward to bus."""
        # Strip bot mention from content
        if self.config.require_mention:
            content = _strip_bot_mention(content, mentions)

        # Sender name injection
        if sender_type == "bot":
            bot_name = next(
                (aid for aid, oid in self._bot_open_ids.items() if oid == sender_id),
                sender_id,
            )
            content = f"[{bot_name}]: {content}" if content else f"[{bot_name}]"
        else:
            client = self._clients.get(account_id)
            if client and sender_id:
                loop = asyncio.get_running_loop()
                name = await loop.run_in_executor(None, self._fetch_sender_name_sync, sender_id, client)
                if name:
                    content = f"[{name}]: {content}" if content else f"[{name}]"

        # 4. Forward to the correct agent (or shared bus as fallback)
        from nanobot.bus.events import InboundMessage
        msg_meta = {**(metadata or {}), "message_id": message_id, "account_id": account_id}
        msg = InboundMessage(
            channel=self.name,
            sender_id=sender_id,
            chat_id=chat_id,
            content=content,
            media=media or [],
            metadata=msg_meta,
        )
        if self.agent_pool:
            await self.agent_pool.route_inbound(msg)
        else:
            await self.bus.publish_inbound(msg)

    def _add_reaction_sync(self, message_id: str, emoji_type: str, client=None) -> str | None:
        """Sync helper for adding reaction (runs in thread pool). Returns reaction_id."""
        import time as _time
        c = client or self._client
        if not c:
            return None
        last_exc = None
        for attempt in range(2):
            try:
                request = CreateMessageReactionRequest.builder() \
                    .message_id(message_id) \
                    .request_body(
                        CreateMessageReactionRequestBody.builder()
                        .reaction_type(Emoji.builder().emoji_type(emoji_type).build())
                        .build()
                    ).build()

                response = c.im.v1.message_reaction.create(request)

                if not response.success():
                    logger.warning("Failed to add reaction: code={}, msg={}", response.code, response.msg)
                    return None
                reaction_id = response.data.reaction_id if response.data else None
                logger.debug("Added {} reaction to message {}, reaction_id={}", emoji_type, message_id, reaction_id)
                return reaction_id
            except Exception as e:
                last_exc = e
                if attempt == 0:
                    logger.debug("Error adding reaction (retrying): {}", e)
                    _time.sleep(1)
        logger.warning("Error adding reaction: {}", last_exc)
        return None

    def _delete_reaction_sync(self, message_id: str, reaction_id: str, client=None) -> None:
        """Sync helper for deleting a reaction (runs in thread pool)."""
        try:
            c = client or self._client
            if not c:
                return

            request = DeleteMessageReactionRequest.builder() \
                .message_id(message_id) \
                .reaction_id(reaction_id) \
                .build()

            response = c.im.v1.message_reaction.delete(request)

            if not response.success():
                logger.warning("Failed to delete reaction: code={}, msg={}", response.code, response.msg)
            else:
                logger.debug("Deleted reaction {} from message {}", reaction_id, message_id)
        except Exception as e:
            logger.warning("Error deleting reaction: {}", e)

    async def _add_reaction(self, message_id: str, emoji_type: str = "THUMBSUP", account_id: str = None) -> str | None:
        """
        Add a reaction emoji to a message (non-blocking). Returns reaction_id.

        Common emoji types: THUMBSUP, OK, EYES, DONE, OnIt, HEART
        """
        if not Emoji:
            return None

        # Get client for account
        client = self._clients.get(account_id) if account_id and self._clients else self._client
        if not client:
            return None

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._add_reaction_sync, message_id, emoji_type, client)

    async def _delete_reaction(self, message_id: str, reaction_id: str, account_id: str = None) -> None:
        """Delete a reaction from a message (non-blocking)."""
        if not Emoji:
            return

        client = self._clients.get(account_id) if account_id and self._clients else self._client
        if not client:
            return

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._delete_reaction_sync, message_id, reaction_id, client)

    # Regex to match markdown tables (header + separator + data rows)
    _TABLE_RE = re.compile(
        r"((?:^[ \t]*\|.+\|[ \t]*\n)(?:^[ \t]*\|[-:\s|]+\|[ \t]*\n)(?:^[ \t]*\|.+\|[ \t]*\n?)+)",
        re.MULTILINE,
    )

    _HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)

    _CODE_BLOCK_RE = re.compile(r"(```[\s\S]*?```)", re.MULTILINE)

    @staticmethod
    def _parse_md_table(table_text: str) -> dict | None:
        """Parse a markdown table into a Feishu table element."""
        lines = [_line.strip() for _line in table_text.strip().split("\n") if _line.strip()]
        if len(lines) < 3:
            return None
        def split(_line: str) -> list[str]:
            return [c.strip() for c in _line.strip("|").split("|")]
        headers = split(lines[0])
        rows = [split(_line) for _line in lines[2:]]
        columns = [{"tag": "column", "name": f"c{i}", "display_name": h, "width": "auto"}
                   for i, h in enumerate(headers)]
        return {
            "tag": "table",
            "page_size": len(rows) + 1,
            "columns": columns,
            "rows": [{f"c{i}": r[i] if i < len(r) else "" for i in range(len(headers))} for r in rows],
        }

    def _build_post_with_mentions(self, content: str) -> dict | None:
        """Convert text with @AccountName patterns to a Feishu post (rich text) payload.

        Returns a post payload dict if any @mention was resolved to a real open_id,
        otherwise returns None (caller should fall back to plain text).

        The name lookup is case-insensitive and tries both account_id and the
        account's display name stored in config.

        Note: Feishu post content must NOT include the outer {"post": ...} wrapper —
        the correct format is {"zh_cn": {"content": [[...]]}} directly.
        """
        # Build name → open_id and open_id → display_name lookups
        name_to_open_id: dict[str, str] = {}
        open_id_to_name: dict[str, str] = {}
        for account_id, open_id in self._bot_open_ids.items():
            name_to_open_id[account_id.lower()] = open_id
            open_id_to_name[open_id] = account_id
            # Also index by config display name if available
            acc_cfg = self.config.accounts.get(account_id)
            if acc_cfg and acc_cfg.name:
                name_to_open_id[acc_cfg.name.lower()] = open_id
                open_id_to_name[open_id] = acc_cfg.name

        # Find all @Name tokens
        at_pattern = re.compile(r"@([\w\u4e00-\u9fff]+)")
        matches = list(at_pattern.finditer(content))
        if not matches:
            return None

        # Check if at least one @Name resolves to a known open_id
        resolved_any = any(m.group(1).lower() in name_to_open_id for m in matches)
        if not resolved_any:
            return None

        # Build post content: split text around @mentions
        elements: list[dict] = []
        last = 0
        for m in matches:
            before = content[last:m.start()]
            if before:
                elements.append({"tag": "text", "text": before})
            name = m.group(1)
            open_id = name_to_open_id.get(name.lower())
            if open_id:
                display_name = open_id_to_name.get(open_id, name)
                elements.append({"tag": "at", "user_id": open_id, "user_name": display_name})
            else:
                # Unknown @name — keep as plain text
                elements.append({"tag": "text", "text": m.group(0)})
            last = m.end()
        tail = content[last:]
        if tail:
            elements.append({"tag": "text", "text": tail})

        # Feishu post content format: {"zh_cn": {"content": [[...]]}}
        # Do NOT wrap in {"post": ...} — that causes error 230001.
        return {"zh_cn": {"content": [elements]}}

    def _build_card_elements(self, content: str) -> list[dict]:
        """Split content into div/markdown + table elements for Feishu card."""
        elements, last_end = [], 0
        for m in self._TABLE_RE.finditer(content):
            before = content[last_end:m.start()]
            if before.strip():
                elements.extend(self._split_headings(before))
            elements.append(self._parse_md_table(m.group(1)) or {"tag": "markdown", "content": m.group(1)})
            last_end = m.end()
        remaining = content[last_end:]
        if remaining.strip():
            elements.extend(self._split_headings(remaining))
        return elements or [{"tag": "markdown", "content": content}]

    def _split_headings(self, content: str) -> list[dict]:
        """Split content by headings, converting headings to div elements."""
        protected = content
        code_blocks = []
        for m in self._CODE_BLOCK_RE.finditer(content):
            code_blocks.append(m.group(1))
            protected = protected.replace(m.group(1), f"\x00CODE{len(code_blocks)-1}\x00", 1)

        elements = []
        last_end = 0
        for m in self._HEADING_RE.finditer(protected):
            before = protected[last_end:m.start()].strip()
            if before:
                elements.append({"tag": "markdown", "content": before})
            text = m.group(2).strip()
            elements.append({
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**{text}**",
                },
            })
            last_end = m.end()
        remaining = protected[last_end:].strip()
        if remaining:
            elements.append({"tag": "markdown", "content": remaining})

        for i, cb in enumerate(code_blocks):
            for el in elements:
                if el.get("tag") == "markdown":
                    el["content"] = el["content"].replace(f"\x00CODE{i}\x00", cb)

        return elements or [{"tag": "markdown", "content": content}]

    _IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".ico", ".tiff", ".tif"}
    _AUDIO_EXTS = {".opus"}
    _FILE_TYPE_MAP = {
        ".opus": "opus", ".mp4": "mp4", ".pdf": "pdf", ".doc": "doc", ".docx": "doc",
        ".xls": "xls", ".xlsx": "xls", ".ppt": "ppt", ".pptx": "ppt",
    }

    def _upload_image_sync(self, file_path: str, client: Any = None) -> str | None:
        """Upload an image to Feishu and return the image_key."""
        if not client:
            client = self._client
        try:
            with open(file_path, "rb") as f:
                request = CreateImageRequest.builder() \
                    .request_body(
                        CreateImageRequestBody.builder()
                        .image_type("message")
                        .image(f)
                        .build()
                    ).build()
                response = client.im.v1.image.create(request)
                if response.success():
                    image_key = response.data.image_key
                    logger.debug("Uploaded image {}: {}", os.path.basename(file_path), image_key)
                    return image_key
                else:
                    logger.error("Failed to upload image: code={}, msg={}", response.code, response.msg)
                    return None
        except Exception as e:
            logger.error("Error uploading image {}: {}", file_path, e)
            return None

    def _upload_file_sync(self, file_path: str, client: Any = None) -> str | None:
        """Upload a file to Feishu and return the file_key."""
        if not client:
            client = self._client
        ext = os.path.splitext(file_path)[1].lower()
        file_type = self._FILE_TYPE_MAP.get(ext, "stream")
        file_name = os.path.basename(file_path)
        try:
            with open(file_path, "rb") as f:
                request = CreateFileRequest.builder() \
                    .request_body(
                        CreateFileRequestBody.builder()
                        .file_type(file_type)
                        .file_name(file_name)
                        .file(f)
                        .build()
                    ).build()
                response = client.im.v1.file.create(request)
                if response.success():
                    file_key = response.data.file_key
                    logger.debug("Uploaded file {}: {}", file_name, file_key)
                    return file_key
                else:
                    logger.error("Failed to upload file: code={}, msg={}", response.code, response.msg)
                    return None
        except Exception as e:
            logger.error("Error uploading file {}: {}", file_path, e)
            return None

    def _download_image_sync(self, message_id: str, image_key: str) -> tuple[bytes | None, str | None]:
        """Download an image from Feishu message by message_id and image_key."""
        try:
            request = GetMessageResourceRequest.builder() \
                .message_id(message_id) \
                .file_key(image_key) \
                .type("image") \
                .build()
            response = self._client.im.v1.message_resource.get(request)
            if response.success():
                file_data = response.file
                # GetMessageResourceRequest returns BytesIO, need to read bytes
                if hasattr(file_data, 'read'):
                    file_data = file_data.read()
                return file_data, response.file_name
            else:
                logger.error("Failed to download image: code={}, msg={}", response.code, response.msg)
                return None, None
        except Exception as e:
            logger.error("Error downloading image {}: {}", image_key, e)
            return None, None

    def _download_file_sync(
        self, message_id: str, file_key: str, resource_type: str = "file"
    ) -> tuple[bytes | None, str | None]:
        """Download a file/audio/media from a Feishu message by message_id and file_key."""
        try:
            request = (
                GetMessageResourceRequest.builder()
                .message_id(message_id)
                .file_key(file_key)
                .type(resource_type)
                .build()
            )
            response = self._client.im.v1.message_resource.get(request)
            if response.success():
                file_data = response.file
                if hasattr(file_data, "read"):
                    file_data = file_data.read()
                return file_data, response.file_name
            else:
                logger.error("Failed to download {}: code={}, msg={}", resource_type, response.code, response.msg)
                return None, None
        except Exception:
            logger.exception("Error downloading {} {}", resource_type, file_key)
            return None, None

    async def _download_and_save_media(
        self,
        msg_type: str,
        content_json: dict,
        message_id: str | None = None
    ) -> tuple[str | None, str]:
        """
        Download media from Feishu and save to local disk.

        Returns:
            (file_path, content_text) - file_path is None if download failed
        """
        loop = asyncio.get_running_loop()
        media_dir = Path.home() / ".nanobot" / "media"
        media_dir.mkdir(parents=True, exist_ok=True)

        data, filename = None, None

        if msg_type == "image":
            image_key = content_json.get("image_key")
            if image_key and message_id:
                data, filename = await loop.run_in_executor(
                    None, self._download_image_sync, message_id, image_key
                )
                if not filename:
                    filename = f"{image_key[:16]}.jpg"

        elif msg_type in ("audio", "file", "media"):
            file_key = content_json.get("file_key")
            if file_key and message_id:
                data, filename = await loop.run_in_executor(
                    None, self._download_file_sync, message_id, file_key, msg_type
                )
                if not filename:
                    ext = {"audio": ".opus", "media": ".mp4"}.get(msg_type, "")
                    filename = f"{file_key[:16]}{ext}"

        if data and filename:
            file_path = media_dir / filename
            file_path.write_bytes(data)
            logger.debug("Downloaded {} to {}", msg_type, file_path)
            return str(file_path), f"[{msg_type}: {filename}]"

        return None, f"[{msg_type}: download failed]"

    def _send_message_sync(self, receive_id_type: str, receive_id: str, msg_type: str, content: str, client: Any = None) -> str | None:
        """Send a single message (text/image/file/interactive) synchronously."""
        import time as _time
        if not client:
            client = self._client
        last_exc = None
        for attempt in range(2):
            try:
                request = CreateMessageRequest.builder() \
                    .receive_id_type(receive_id_type) \
                    .request_body(
                        CreateMessageRequestBody.builder()
                        .receive_id(receive_id)
                        .msg_type(msg_type)
                        .content(content)
                        .build()
                    ).build()
                response = client.im.v1.message.create(request)
                if not response.success():
                    logger.error(
                        "Failed to send Feishu {} message: code={}, msg={}, log_id={}",
                        msg_type, response.code, response.msg, response.get_log_id()
                    )
                    return None
                msg_id = response.data.message_id if response.data else None
                logger.debug("Feishu {} message sent to {}, msg_id={}", msg_type, receive_id, msg_id)
                return msg_id
            except Exception as e:
                last_exc = e
                if attempt == 0:
                    logger.warning("Error sending Feishu {} message (retrying): {}", msg_type, e)
                    _time.sleep(1)
        logger.error("Error sending Feishu {} message: {}", msg_type, last_exc)
        return None

    def _record_sent(self, account_id: str | None, message_id: str | None) -> None:
        """Store a sent message_id to prevent self-echo processing."""
        if not account_id or not message_id:
            return
        cache = self._sent_message_ids.setdefault(account_id, OrderedDict())
        cache[message_id] = None
        while len(cache) > 500:
            cache.popitem(last=False)

    async def send(self, msg: OutboundMessage) -> None:
        """Send a message through Feishu, including media (images/files) if present."""
        # Skip progress messages for reaction management
        is_progress = msg.metadata.get("_progress") if msg.metadata else False

        # Get client for account (backward compat: use _client if _clients empty)
        account_id = msg.metadata.get("account_id") if msg.metadata else None
        client = self._clients.get(account_id) if account_id and self._clients else (self._clients.get(next(iter(self._clients))) if self._clients else self._client)

        if not client:
            logger.warning("Feishu client not initialized")
            return

        try:
            receive_id_type = "chat_id" if msg.chat_id.startswith("oc_") else "open_id"
            loop = asyncio.get_running_loop()

            for file_path in msg.media:
                if not os.path.isfile(file_path):
                    logger.warning("Media file not found: {}", file_path)
                    continue
                ext = os.path.splitext(file_path)[1].lower()
                if ext in self._IMAGE_EXTS:
                    key = await loop.run_in_executor(None, self._upload_image_sync, file_path, client)
                    if key:
                        await loop.run_in_executor(
                            None, self._send_message_sync,
                            receive_id_type, msg.chat_id, "image", json.dumps({"image_key": key}, ensure_ascii=False), client,
                        )
                else:
                    key = await loop.run_in_executor(None, self._upload_file_sync, file_path, client)
                    if key:
                        media_type = "audio" if ext in self._AUDIO_EXTS else "file"
                        await loop.run_in_executor(
                            None, self._send_message_sync,
                            receive_id_type, msg.chat_id, media_type, json.dumps({"file_key": key}, ensure_ascii=False), client,
                        )

            if msg.content and msg.content.strip():
                render_mode = self.config.render_mode
                # Try to convert @mentions to real Feishu at-elements first
                post_payload = self._build_post_with_mentions(msg.content)
                if post_payload:
                    logger.debug("Sending post with mentions: {}", post_payload)
                    sent_id = await loop.run_in_executor(
                        None, self._send_message_sync,
                        receive_id_type, msg.chat_id, "post", json.dumps(post_payload, ensure_ascii=False), client,
                    )
                    self._record_sent(account_id, sent_id)
                else:
                    use_card = (
                        render_mode == "card" or
                        (render_mode == "auto" and _should_use_card(msg.content))
                    )
                    if use_card:
                        card = {"config": {"wide_screen_mode": True}, "elements": self._build_card_elements(msg.content)}
                        sent_id = await loop.run_in_executor(
                            None, self._send_message_sync,
                            receive_id_type, msg.chat_id, "interactive", json.dumps(card, ensure_ascii=False), client,
                        )
                        self._record_sent(account_id, sent_id)
                    else:
                        sent_id = await loop.run_in_executor(
                            None, self._send_message_sync,
                            receive_id_type, msg.chat_id, "text", json.dumps({"text": msg.content}, ensure_ascii=False), client,
                        )
                        self._record_sent(account_id, sent_id)

            # Delete the "thinking" reaction after final reply (not on progress messages)
            if not is_progress and msg.metadata:
                orig_message_id = msg.metadata.get("message_id")
                reaction_id = msg.metadata.get("reaction_id")
                if orig_message_id and reaction_id:
                    await self._delete_reaction(orig_message_id, reaction_id, account_id)

        except Exception as e:
            logger.error("Error sending Feishu message: {}", e)

    def _on_message_sync(self, data: "P2ImMessageReceiveV1", account_id: str) -> None:
        """
        Sync handler for incoming messages (called from WebSocket thread).
        Schedules async handling in the main event loop.
        """
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._on_message(data, account_id), self._loop)

    async def _on_message(self, data: "P2ImMessageReceiveV1", account_id: str) -> None:
        """Handle incoming message from Feishu."""
        try:
            event = data.event
            message = event.message
            sender = event.sender
            # Deduplication check — keyed per account so multiple bots in the same
            # group each process the message independently.
            message_id = message.message_id
            dedup_key = f"{message_id}:{account_id}"
            if dedup_key in self._processed_message_ids:
                return
            self._processed_message_ids[dedup_key] = None

            # Trim cache
            while len(self._processed_message_ids) > 1000:
                self._processed_message_ids.popitem(last=False)

            # Skip bot messages
            if sender.sender_type == "bot":
                # Ignore our own echoed messages
                if message_id in self._sent_message_ids.get(account_id, {}):
                    return
                # For other bots' messages: only process if this bot is @mentioned
                # (require_mention check happens below in the group branch)
                # If not a group message, skip all bot DMs
                if message.chat_type != "group":
                    return

            # Route to agent via AgentPool
            if self.agent_pool:
                agent = self.agent_pool.get_agent("feishu", account_id)
                if not agent:
                    logger.warning("No agent found for feishu account {}", account_id)
                    return

            sender_id = sender.sender_id.open_id if sender.sender_id else "unknown"
            chat_id = message.chat_id
            chat_type = message.chat_type
            msg_type = message.message_type
            logger.debug("Feishu message: chat_type={} chat_id={} sender_id={}", chat_type, chat_id, sender_id)

            # Parse content first (before adding reaction, so we can skip early)
            content_parts = []
            media_paths = []

            try:
                content_json = json.loads(message.content) if message.content else {}
            except json.JSONDecodeError:
                content_json = {}

            if msg_type == "text":
                text = content_json.get("text", "")
                if text:
                    content_parts.append(text)

            elif msg_type == "post":
                text, image_keys = _extract_post_content(content_json)
                if text:
                    content_parts.append(text)
                # Download images embedded in post
                for img_key in image_keys:
                    file_path, content_text = await self._download_and_save_media(
                        "image", {"image_key": img_key}, message_id
                    )
                    if file_path:
                        media_paths.append(file_path)
                    content_parts.append(content_text)

            elif msg_type in ("image", "audio", "file", "media"):
                file_path, content_text = await self._download_and_save_media(msg_type, content_json, message_id)
                if file_path:
                    media_paths.append(file_path)
                content_parts.append(content_text)

            elif msg_type in ("share_chat", "share_user", "interactive", "share_calendar_event", "system", "merge_forward"):
                # Handle share cards and interactive messages
                text = _extract_share_card_content(content_json, msg_type)
                if text:
                    content_parts.append(text)

            else:
                content_parts.append(MSG_TYPE_MAP.get(msg_type, f"[{msg_type}]"))

            content = "\n".join(content_parts) if content_parts else ""

            # Fetch quoted/replied message content if this is a reply
            parent_id = getattr(message, "parent_id", None)
            if parent_id and (content or media_paths):
                client = self._clients.get(account_id)
                if client:
                    loop = asyncio.get_running_loop()
                    quoted = await loop.run_in_executor(
                        None, self._fetch_message_content_sync, parent_id, client
                    )
                    if quoted:
                        content = f'[Replying to: "{quoted}"]\n\n{content}'

            if not content and not media_paths:
                return

            # For group messages, check allowlist and mention before adding reaction
            if chat_type == "group":
                mentions_raw: list[dict] = []
                if hasattr(message, "mentions") and message.mentions:
                    mentions_raw = [
                        {
                            "key": m.key,
                            "id": {"open_id": m.id.open_id if m.id else ""},
                            "name": m.name,
                        }
                        for m in message.mentions
                    ]
                group_allow_from = self.config.group_allow_from
                if group_allow_from and chat_id not in group_allow_from:
                    logger.debug("Feishu group {} not in group_allow_from, skipping", chat_id)
                    return
                if self.config.require_mention:
                    bot_open_id = self._bot_open_ids.get(account_id)
                    if not _check_bot_mentioned(mentions_raw, bot_open_id):
                        logger.debug("Feishu group message in {} not mentioning bot, skipping", chat_id)
                        return

            # Add reaction now that we know we'll process this message
            reaction_id = await self._add_reaction(message_id, self.config.react_emoji, account_id)

            # Forward to message bus
            base_metadata = {
                "message_id": message_id,
                "reaction_id": reaction_id,
                "chat_type": chat_type,
                "msg_type": msg_type,
                "account_id": account_id,
            }
            if chat_type == "group":
                await self._handle_group_message(
                    sender_id=sender_id,
                    chat_id=chat_id,
                    content=content,
                    message_id=message_id,
                    mentions=mentions_raw,
                    account_id=account_id,
                    media=media_paths,
                    metadata=base_metadata,
                    sender_type=sender.sender_type,
                )
            else:
                await self._handle_message(
                    sender_id=sender_id,
                    chat_id=sender_id,
                    content=content,
                    media=media_paths,
                    metadata=base_metadata,
                )

        except Exception as e:
            logger.error("Error processing Feishu message: {}", e)


