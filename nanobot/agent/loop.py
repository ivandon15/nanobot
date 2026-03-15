"""Agent loop: the core processing engine."""

from __future__ import annotations

import asyncio
import json
import re
import threading
import weakref
from contextlib import AsyncExitStack
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from loguru import logger

from nanobot.agent.context import ContextBuilder
from nanobot.agent.memory import MemoryStore
from nanobot.agent.subagent import SubagentManager
from nanobot.agent.tools.cron import CronTool
from nanobot.agent.tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from nanobot.agent.tools.message import MessageTool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.shell import ExecTool
from nanobot.agent.tools.spawn import SpawnTool
from nanobot.agent.tools.web import WebFetchTool, WebSearchTool
from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMProvider
from nanobot.session.manager import Session, SessionManager

if TYPE_CHECKING:
    from nanobot.config.schema import ChannelsConfig, ChromeConfig, ExecToolConfig, OpenVikingConfig
    from nanobot.cron.service import CronService


class AgentLoop:
    """
    The agent loop is the core processing engine.

    It:
    1. Receives messages from the bus
    2. Builds context with history, memory, skills
    3. Calls the LLM
    4. Executes tool calls
    5. Sends responses back
    """

    _TOOL_RESULT_MAX_CHARS = 500

    def __init__(
        self,
        bus: MessageBus,
        provider: LLMProvider,
        workspace: Path,
        model: str | None = None,
        max_iterations: int = 40,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        memory_window: int = 100,
        reasoning_effort: str | None = None,
        image_provider: "LLMProvider | None" = None,
        brave_api_key: str | None = None,
        web_proxy: str | None = None,
        exec_config: ExecToolConfig | None = None,
        cron_service: CronService | None = None,
        restrict_to_workspace: bool = False,
        session_manager: SessionManager | None = None,
        mcp_servers: dict | None = None,
        channels_config: ChannelsConfig | None = None,
        openviking_config: OpenVikingConfig | None = None,
        chrome_config: ChromeConfig | None = None,
        agent_id: str | None = None,
        agent_pool: Any | None = None,
        image_gen_model: str | None = None,
        image_gen_api_key: str | None = None,
        image_gen_api_base: str | None = None,
    ):
        from nanobot.config.schema import ExecToolConfig
        self.bus = bus
        self.channels_config = channels_config
        self.provider = provider
        self.workspace = workspace
        self.model = model or provider.get_default_model()
        self.max_iterations = max_iterations
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.memory_window = memory_window
        self.reasoning_effort = reasoning_effort
        self.image_provider = image_provider
        self.image_gen_model = image_gen_model
        self.image_gen_api_key = image_gen_api_key
        self.image_gen_api_base = image_gen_api_base
        self.brave_api_key = brave_api_key
        self.web_proxy = web_proxy
        self.exec_config = exec_config or ExecToolConfig()
        self.cron_service = cron_service
        self.restrict_to_workspace = restrict_to_workspace
        self.openviking_config = openviking_config
        self.chrome_config = chrome_config
        self._agent_id = agent_id
        self._agent_pool = agent_pool

        ov_data_path = (
            self.openviking_config.resolved_data_path(workspace)
            if self.openviking_config and self.openviking_config.enabled
            else None
        )
        self.context = ContextBuilder(workspace, ov_data_path=ov_data_path)
        self.sessions = session_manager or SessionManager(workspace)
        self.tools = ToolRegistry()
        self.subagents = SubagentManager(
            provider=provider,
            workspace=workspace,
            bus=bus,
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            reasoning_effort=reasoning_effort,
            brave_api_key=brave_api_key,
            web_proxy=web_proxy,
            exec_config=self.exec_config,
            restrict_to_workspace=restrict_to_workspace,
        )

        self._running = False
        self._mcp_servers = mcp_servers or {}
        self._mcp_stack: AsyncExitStack | None = None
        self._mcp_connected = False
        self._mcp_connecting = False
        self._consolidating: set[str] = set()  # Session keys with consolidation in progress
        self._consolidation_tasks: set[asyncio.Task] = set()  # Strong refs to in-flight tasks
        self._consolidation_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
        self._session_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
        self._active_tasks: dict[str, list[asyncio.Task]] = {}  # session_key -> tasks
        self._cancel_events: dict[str, asyncio.Event] = {}  # session_key -> cancel event
        self._current_context: tuple[str, str, dict] | None = None  # (channel, chat_id, metadata)
        self._inbound: asyncio.Queue[InboundMessage] = asyncio.Queue()  # Per-agent inbound queue
        self._register_default_tools()

    def _get_peer_names(self) -> list[str]:
        """Return account names of all known peer agents (for MessageTool guard)."""
        if not self._agent_pool or not self._agent_id or not self._current_context:
            return []
        channel, chat_id, _ = self._current_context
        peers = self._agent_pool.get_peer_agents(chat_id, self._agent_id)
        names = []
        for aid in peers:
            account = self._agent_pool.get_agent_account(channel, aid)
            names.append(account or aid)
        return names

    def _refresh_discuss_tool(self, chat_id: str) -> None:
        """Register or update DiscussTool with current group peers."""
        if not self._agent_pool or not self._agent_id:
            return
        from nanobot.agent.tools.discuss import DiscussTool
        peers = self._agent_pool.get_peer_agents(chat_id, self._agent_id)
        if not peers:
            return
        if self.tools.get("discuss_with_agents") is None:
            self.tools.register(DiscussTool(
                get_peers=lambda cid=chat_id: self._agent_pool.get_peer_agents(cid, self._agent_id),
                bus=self.bus,
                get_context=lambda: self._current_context,
                get_agent_account=lambda ch, aid: self._agent_pool.get_agent_account(ch, aid),
            ))

    def _register_default_tools(self) -> None:
        """Register the default set of tools."""
        allowed_dir = self.workspace if self.restrict_to_workspace else None
        for cls in (ReadFileTool, WriteFileTool, EditFileTool, ListDirTool):
            self.tools.register(cls(workspace=self.workspace, allowed_dir=allowed_dir))
        self.tools.register(ExecTool(
            working_dir=str(self.workspace),
            timeout=self.exec_config.timeout,
            restrict_to_workspace=self.restrict_to_workspace,
            path_append=self.exec_config.path_append,
        ))
        self.tools.register(WebSearchTool(api_key=self.brave_api_key, proxy=self.web_proxy))
        self.tools.register(WebFetchTool(proxy=self.web_proxy))
        self.tools.register(MessageTool(
            send_callback=self.bus.publish_outbound,
            get_peer_names=self._get_peer_names,
        ))
        self.tools.register(SpawnTool(manager=self.subagents))
        if self.cron_service:
            self.tools.register(CronTool(self.cron_service))
        if self.openviking_config and self.openviking_config.enabled:
            from nanobot.agent.tools.openviking import OV_TOOLS
            for cls in OV_TOOLS:
                self.tools.register(cls(data_path=self.openviking_config.resolved_data_path(self.workspace)))
        if self.channels_config and self.channels_config.feishu.enabled:
            from nanobot.agent.tools.feishu import register_feishu_tools
            register_feishu_tools(self.tools, self.channels_config.feishu, account_id=self._agent_id)
        if self.chrome_config and self.chrome_config.enabled:
            from nanobot.agent.tools.chrome import ChromeTool
            self.tools.register(ChromeTool(host=self.chrome_config.cdp_host, port=self.chrome_config.cdp_port))
        if self.image_gen_model and self.image_gen_api_key:
            from nanobot.agent.tools.image import GenerateImageTool
            self.tools.register(GenerateImageTool(
                model=self.image_gen_model,
                api_key=self.image_gen_api_key,
                api_base=self.image_gen_api_base or "https://openrouter.ai/api",
                workspace=self.workspace,
            ))

    async def _connect_mcp(self) -> None:
        """Connect to configured MCP servers (one-time, lazy)."""
        if self._mcp_connected or self._mcp_connecting or not self._mcp_servers:
            return
        self._mcp_connecting = True
        from nanobot.agent.tools.mcp import connect_mcp_servers
        try:
            self._mcp_stack = AsyncExitStack()
            await self._mcp_stack.__aenter__()
            await connect_mcp_servers(self._mcp_servers, self.tools, self._mcp_stack)
            self._mcp_connected = True
        except Exception as e:
            logger.error("Failed to connect MCP servers (will retry next message): {}", e)
            if self._mcp_stack:
                try:
                    await self._mcp_stack.aclose()
                except Exception:
                    pass
                self._mcp_stack = None
        finally:
            self._mcp_connecting = False

    def _set_tool_context(self, channel: str, chat_id: str, message_id: str | None = None, metadata: dict | None = None) -> None:
        """Update context for all tools that need routing info."""
        for name in ("message", "spawn", "cron"):
            if tool := self.tools.get(name):
                if hasattr(tool, "set_context"):
                    if name == "message":
                        tool.set_context(channel, chat_id, message_id, metadata=metadata)
                    else:
                        tool.set_context(channel, chat_id)
        for name in ("openviking_read", "openviking_list", "openviking_search",
                     "openviking_grep", "openviking_glob", "user_memory_search"):
            if tool := self.tools.get(name):
                if hasattr(tool, "set_context"):
                    tool.set_context(channel, chat_id)

    @staticmethod
    def _strip_think(text: str | None) -> str | None:
        """Remove <think>…</think> blocks that some models embed in content."""
        if not text:
            return None
        return re.sub(r"<think>[\s\S]*?</think>", "", text).strip() or None

    @staticmethod
    def _tool_hint(tool_calls: list) -> str:
        """Format tool calls as concise hint, e.g. 'web_search("query")'."""
        def _fmt(tc):
            args = (tc.arguments[0] if isinstance(tc.arguments, list) else tc.arguments) or {}
            val = next(iter(args.values()), None) if isinstance(args, dict) else None
            if not isinstance(val, str):
                return tc.name
            return f'{tc.name}("{val[:40]}…")' if len(val) > 40 else f'{tc.name}("{val}")'
        return ", ".join(_fmt(tc) for tc in tool_calls)

    async def _run_agent_loop(
        self,
        initial_messages: list[dict],
        on_progress: Callable[..., Awaitable[None]] | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> tuple[str | None, list[str], list[dict]]:
        """Run the agent iteration loop. Returns (final_content, tools_used, messages)."""
        messages = initial_messages
        iteration = 0
        final_content = None
        tools_used: list[str] = []

        while iteration < self.max_iterations:
            iteration += 1

            if cancel_event and cancel_event.is_set():
                return "⏹ Task cancelled.", tools_used, messages

            # Use image_provider when the current turn has images
            has_images = any(
                isinstance(m.get("content"), list) and
                any(c.get("type") == "image_url" for c in m["content"])
                for m in messages if m.get("role") == "user"
            )
            active_provider = (
                self.image_provider if (has_images and self.image_provider) else self.provider
            )
            response = await active_provider.chat(
                messages=messages,
                tools=self.tools.get_definitions(),
                model=self.model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                reasoning_effort=self.reasoning_effort,
            )

            if response.has_tool_calls:
                if on_progress:
                    clean = self._strip_think(response.content)
                    tool_names = {tc.name for tc in response.tool_calls}
                    if clean and "message" not in tool_names:
                        await on_progress(clean)
                    await on_progress(self._tool_hint(response.tool_calls), tool_hint=True)

                tool_call_dicts = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False)
                        }
                    }
                    for tc in response.tool_calls
                ]
                messages = self.context.add_assistant_message(
                    messages, response.content, tool_call_dicts,
                    reasoning_content=response.reasoning_content,
                    thinking_blocks=response.thinking_blocks,
                )

                for tool_call in response.tool_calls:
                    if cancel_event and cancel_event.is_set():
                        return "⏹ Task cancelled.", tools_used, messages
                    tools_used.append(tool_call.name)
                    args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
                    logger.info("Tool call: {}({})", tool_call.name, args_str[:200])
                    result = await self.tools.execute(tool_call.name, tool_call.arguments)
                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
                    )
            else:
                clean = self._strip_think(response.content)
                # Don't persist error responses to session history — they can
                # poison the context and cause permanent 400 loops (#1303).
                if response.finish_reason == "error":
                    logger.error("LLM returned error: {}", (clean or "")[:200])
                    final_content = clean or "Sorry, I encountered an error calling the AI model."
                    break
                messages = self.context.add_assistant_message(
                    messages, clean, reasoning_content=response.reasoning_content,
                    thinking_blocks=response.thinking_blocks,
                )
                final_content = clean
                break

        if final_content is None and iteration >= self.max_iterations:
            logger.warning("Max iterations ({}) reached", self.max_iterations)
            final_content = (
                f"I reached the maximum number of tool call iterations ({self.max_iterations}) "
                "without completing the task. You can try breaking the task into smaller steps."
            )

        return final_content, tools_used, messages

    async def enqueue(self, msg: InboundMessage) -> None:
        """Put a message directly into this agent's inbound queue."""
        await self._inbound.put(msg)

    async def run(self) -> None:
        """Run the agent loop, dispatching messages as tasks to stay responsive to /stop."""
        self._running = True
        await self._connect_mcp()
        logger.info("Agent loop started")

        while self._running:
            try:
                msg = await asyncio.wait_for(self._inbound.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            if self._is_stop_command(msg.content):
                await self._handle_stop(msg)
            else:
                task = asyncio.create_task(self._dispatch(msg))
                self._active_tasks.setdefault(msg.session_key, []).append(task)
                task.add_done_callback(lambda t, k=msg.session_key: self._active_tasks.get(k, []) and self._active_tasks[k].remove(t) if t in self._active_tasks.get(k, []) else None)

    @staticmethod
    def _is_stop_command(content: str) -> bool:
        """Check if content is a /stop command, handling group [Name]: prefix."""
        stripped = content.strip()
        # Strip [Name]: prefix injected for group messages
        if stripped.startswith("[") and "]: " in stripped:
            stripped = stripped.split("]: ", 1)[1].strip()
        return stripped.lower() == "/stop"

    async def _handle_stop(self, msg: InboundMessage) -> None:
        """Cancel all active tasks and subagents for the session."""
        event = self._cancel_events.get(msg.session_key)
        if event:
            event.set()
        tasks = self._active_tasks.pop(msg.session_key, [])
        cancelled = sum(1 for t in tasks if not t.done() and t.cancel())
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        sub_cancelled = await self.subagents.cancel_by_session(msg.session_key)
        total = cancelled + sub_cancelled
        content = f"⏹ Stopped {total} task(s)." if total else "No active task to stop."
        await self.bus.publish_outbound(OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content=content,
        ))

    async def _dispatch(self, msg: InboundMessage) -> None:
        """Process a message under a per-session lock."""
        lock = self._session_locks.setdefault(msg.session_key, asyncio.Lock())
        async with lock:
            try:
                response = await self._process_message(msg)
                if response is not None:
                    await self.bus.publish_outbound(response)
                elif msg.channel == "cli":
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel, chat_id=msg.chat_id,
                        content="", metadata=msg.metadata or {},
                    ))
            except asyncio.CancelledError:
                logger.info("Task cancelled for session {}", msg.session_key)
                raise
            except Exception:
                logger.exception("Error processing message for session {}", msg.session_key)
                await self.bus.publish_outbound(OutboundMessage(
                    channel=msg.channel, chat_id=msg.chat_id,
                    content="Sorry, I encountered an error.",
                ))

    async def close_mcp(self) -> None:
        """Close MCP connections."""
        if self._mcp_stack:
            try:
                await self._mcp_stack.aclose()
            except (RuntimeError, BaseExceptionGroup):
                pass  # MCP SDK cancel scope cleanup is noisy but harmless
            self._mcp_stack = None

    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        logger.info("Agent loop stopping")

    async def _process_message(
        self,
        msg: InboundMessage,
        session_key: str | None = None,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> OutboundMessage | None:
        """Process a single inbound message and return the response."""
        # System messages: parse origin from chat_id ("channel:chat_id")
        if msg.channel == "system":
            channel, chat_id = (msg.chat_id.split(":", 1) if ":" in msg.chat_id
                                else ("cli", msg.chat_id))
            logger.info("Processing system message from {}", msg.sender_id)
            key = f"{channel}:{chat_id}"
            session = self.sessions.get_or_create(key)
            self._set_tool_context(channel, chat_id, msg.metadata.get("message_id"), metadata=msg.metadata)
            history = session.get_history(max_messages=self.memory_window)
            messages = await self.context.build_messages(
                history=history,
                current_message=msg.content, channel=channel, chat_id=chat_id,
            )
            cancel_event = asyncio.Event()
            self._cancel_events[key] = cancel_event
            try:
                final_content, _, all_msgs = await self._run_agent_loop(messages, cancel_event=cancel_event)
            finally:
                self._cancel_events.pop(key, None)
            self._save_turn(session, all_msgs, 1 + len(history))
            self.sessions.save(session)
            return OutboundMessage(channel=channel, chat_id=chat_id,
                                  content=final_content or "Background task completed.")

        preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
        logger.info("Processing message from {}:{}: {}", msg.channel, msg.sender_id, preview)

        key = session_key or msg.session_key
        session = self.sessions.get_or_create(key)

        # Store current context so DiscussTool can send visible group messages
        self._current_context = (msg.channel, msg.chat_id, msg.metadata or {})

        if msg.metadata.get("chat_type") == "group" and self._agent_pool and self._agent_id:
            self._agent_pool.register_group_member(msg.chat_id, self._agent_id)
            self._refresh_discuss_tool(msg.chat_id)

        # Slash commands
        cmd = msg.content.strip().lower()
        if cmd == "/new":
            lock = self._consolidation_locks.setdefault(session.key, asyncio.Lock())
            self._consolidating.add(session.key)
            try:
                async with lock:
                    snapshot = session.messages[session.last_consolidated:]
                    if snapshot:
                        temp = Session(key=session.key)
                        temp.messages = list(snapshot)
                        if not await self._consolidate_memory(temp, archive_all=True):
                            return OutboundMessage(
                                channel=msg.channel, chat_id=msg.chat_id,
                                content="Memory archival failed, session not cleared. Please try again.",
                            )
            except Exception:
                logger.exception("/new archival failed for {}", session.key)
                return OutboundMessage(
                    channel=msg.channel, chat_id=msg.chat_id,
                    content="Memory archival failed, session not cleared. Please try again.",
                )
            finally:
                self._consolidating.discard(session.key)

            session.clear()
            self.sessions.save(session)
            self.sessions.invalidate(session.key)
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id,
                                  content="New session started.")
        if cmd == "/help":
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id,
                                  content="🐈 nanobot commands:\n/new — Start a new conversation\n/stop — Stop the current task\n/help — Show available commands")

        unconsolidated = len(session.messages) - session.last_consolidated
        if (unconsolidated >= self.memory_window and session.key not in self._consolidating):
            self._consolidating.add(session.key)
            lock = self._consolidation_locks.setdefault(session.key, asyncio.Lock())

            async def _consolidate_and_unlock():
                try:
                    async with lock:
                        await self._consolidate_memory(session)
                finally:
                    self._consolidating.discard(session.key)
                    _task = asyncio.current_task()
                    if _task is not None:
                        self._consolidation_tasks.discard(_task)

            _task = asyncio.create_task(_consolidate_and_unlock())
            self._consolidation_tasks.add(_task)

        self._set_tool_context(msg.channel, msg.chat_id, msg.metadata.get("message_id"), metadata=msg.metadata)
        if message_tool := self.tools.get("message"):
            if isinstance(message_tool, MessageTool):
                message_tool.start_turn()

        history = session.get_history(max_messages=self.memory_window)
        peer_names = None
        if self._agent_pool and self._agent_id and msg.metadata.get("chat_type") == "group":
            peers = self._agent_pool.get_peer_agents(msg.chat_id, self._agent_id)
            # Build (agent_id, account_name) pairs so the prompt can show @mention names
            peer_names = [
                (aid, self._agent_pool.get_agent_account(msg.channel, aid) or aid)
                for aid in peers.keys()
            ]
        initial_messages = await self.context.build_messages(
            history=history,
            current_message=msg.content,
            media=msg.media if msg.media else None,
            channel=msg.channel, chat_id=msg.chat_id,
            peer_agent_names=peer_names,
        )

        async def _bus_progress(content: str, *, tool_hint: bool = False) -> None:
            meta = dict(msg.metadata or {})
            meta["_progress"] = True
            meta["_tool_hint"] = tool_hint
            await self.bus.publish_outbound(OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id, content=content, metadata=meta,
            ))

        cancel_event = asyncio.Event()
        self._cancel_events[key] = cancel_event
        try:
            final_content, _, all_msgs = await self._run_agent_loop(
                initial_messages, on_progress=on_progress or _bus_progress, cancel_event=cancel_event,
            )
        finally:
            self._cancel_events.pop(key, None)

        if final_content is None:
            final_content = "I've completed processing but have no response to give."

        self._save_turn(session, all_msgs, 1 + len(history))
        self.sessions.save(session)

        if self.openviking_config and self.openviking_config.auto_commit:
            data_path = self.openviking_config.resolved_data_path(self.workspace)
            _thread = threading.Thread(
                target=self._commit_to_ov_in_thread,
                args=(data_path, msg.channel, msg.chat_id, msg.content, final_content),
                daemon=True,
            )
            _thread.start()

        if (mt := self.tools.get("message")) and isinstance(mt, MessageTool) and mt._sent_in_turn:
            return None

        preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
        logger.info("Response to {}:{}: {}", msg.channel, msg.sender_id, preview)
        return OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content=final_content,
            metadata=msg.metadata or {},
        )

    def _commit_to_ov_in_thread(self, data_path: str, channel: str, chat_id: str, user_content: str, assistant_content: str) -> None:
        """Run OpenViking commit in a separate thread with its own event loop."""
        import asyncio as _asyncio
        loop = _asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                self._commit_to_ov(data_path, channel, chat_id, user_content, assistant_content)
            )
        finally:
            loop.close()

    async def _commit_to_ov(self, data_path: str, channel: str, chat_id: str, user_content: str, assistant_content: str) -> None:
        """Add the current turn to OpenViking and commit the session."""
        if not self.openviking_config:
            return
        try:
            from nanobot.agent.tools.openviking_client import get_client
            client = await get_client(data_path)
            session_id = f"{channel}:{chat_id}"
            await client.add_message(session_id=session_id, role="user", content=user_content)
            await client.add_message(session_id=session_id, role="assistant", content=assistant_content)
            await client.commit_session(session_id)
            logger.debug("OpenViking session committed: {}", session_id)
        except Exception as e:
            logger.warning("OpenViking commit failed: {}", e)

    def _save_turn(self, session: Session, messages: list[dict], skip: int) -> None:
        """Save new-turn messages into session, truncating large tool results."""
        from datetime import datetime
        for m in messages[skip:]:
            entry = dict(m)
            role, content = entry.get("role"), entry.get("content")
            if role == "assistant" and not content and not entry.get("tool_calls"):
                continue  # skip empty assistant messages — they poison session context
            if role == "tool" and isinstance(content, str) and len(content) > self._TOOL_RESULT_MAX_CHARS:
                entry["content"] = content[:self._TOOL_RESULT_MAX_CHARS] + "\n... (truncated)"
            elif role == "user":
                if isinstance(content, str) and content.startswith(ContextBuilder._RUNTIME_CONTEXT_TAG):
                    # Strip the runtime-context prefix, keep only the user text.
                    parts = content.split("\n\n", 1)
                    if len(parts) > 1 and parts[1].strip():
                        entry["content"] = parts[1]
                    else:
                        continue
                if isinstance(content, list):
                    filtered = []
                    for c in content:
                        if c.get("type") == "text" and isinstance(c.get("text"), str) and c["text"].startswith(ContextBuilder._RUNTIME_CONTEXT_TAG):
                            continue  # Strip runtime context from multimodal messages
                        if (c.get("type") == "image_url"
                                and c.get("image_url", {}).get("url", "").startswith("data:image/")):
                            filtered.append({"type": "text", "text": "[image]"})
                        else:
                            filtered.append(c)
                    if not filtered:
                        continue
                    entry["content"] = filtered
            entry.setdefault("timestamp", datetime.now().isoformat())
            session.messages.append(entry)
        session.updated_at = datetime.now()

    async def _consolidate_memory(self, session, archive_all: bool = False) -> bool:
        """Delegate to MemoryStore.consolidate(). Returns True on success."""
        return await MemoryStore(self.workspace).consolidate(
            session, self.provider, self.model,
            archive_all=archive_all, memory_window=self.memory_window,
        )

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        on_progress: Callable[[str], Awaitable[None]] | None = None,
        reply_channel: str | None = None,
        reply_chat_id: str | None = None,
        reply_metadata: dict | None = None,
        inbound_metadata: dict | None = None,
    ) -> str:
        """Process a message directly (for CLI or cron usage)."""
        await self._connect_mcp()
        msg = InboundMessage(channel=channel, sender_id="user", chat_id=chat_id, content=content,
                             metadata=inbound_metadata or {})
        lock = self._session_locks.setdefault(session_key, asyncio.Lock())
        async with lock:
            response = await self._process_message(msg, session_key=session_key, on_progress=on_progress)

        # If _process_message returned None it means the agent used the message tool
        # to send directly. Recover the content from the tool so callers (e.g. DiscussTool)
        # still get the actual response text.
        if response is None:
            mt = self.tools.get("message")
            from nanobot.agent.tools.message import MessageTool
            result = (mt._last_content or "") if isinstance(mt, MessageTool) else ""
        else:
            result = response.content or ""

        # If reply coordinates are given, publish the response visibly to the group
        # using the peer's own account_id so it appears from the right Feishu account.
        # Only do this when the agent didn't already send via the message tool.
        if reply_channel and reply_chat_id and result and response is not None:
            meta = dict(reply_metadata or {})
            await self.bus.publish_outbound(OutboundMessage(
                channel=reply_channel, chat_id=reply_chat_id,
                content=result, metadata=meta,
            ))

        return result
