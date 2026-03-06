"""DiscussTool — consult peer agents in the same group chat."""
from __future__ import annotations
import asyncio
from typing import TYPE_CHECKING, Any, Callable
from loguru import logger
from nanobot.agent.tools.base import Tool

if TYPE_CHECKING:
    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.queue import MessageBus


class DiscussTool(Tool):
    """Consult other agents sharing this group chat for their perspective."""

    @property
    def name(self) -> str:
        return "discuss_with_agents"

    @property
    def description(self) -> str:
        return (
            "Consult other agents in this group chat for their perspective on a question. "
            "Use when the question benefits from multiple viewpoints or specialized knowledge. "
            "Do NOT use for simple questions you can answer alone. "
            "Returns each agent's response; you synthesize and reply to the user."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "The question to discuss."},
                "agent_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Agent IDs to consult.",
                },
            },
            "required": ["question", "agent_ids"],
        }

    def __init__(
        self,
        peers: dict[str, "AgentLoop"] | None = None,
        get_peers: Callable[[], dict[str, "AgentLoop"]] | None = None,
        timeout: float = 60.0,
        bus: "MessageBus | None" = None,
        get_context: "Callable[[], tuple[str, str, dict] | None] | None" = None,
        get_agent_account: "Callable[[str, str], str | None] | None" = None,
    ):
        self._static_peers = peers or {}
        self._get_peers = get_peers
        self.timeout = timeout
        self._bus = bus
        self._get_context = get_context
        self._get_agent_account = get_agent_account

    def _peers(self) -> dict[str, "AgentLoop"]:
        return self._get_peers() if self._get_peers is not None else self._static_peers

    async def execute(self, **kwargs: Any) -> str:
        question: str = kwargs["question"]
        agent_ids: list[str] = kwargs["agent_ids"]

        peers = self._peers()
        ctx = self._get_context() if self._get_context else None

        # Resolve agent IDs: exact match first, then case-insensitive prefix/suffix fallback.
        def _resolve(aid: str) -> str:
            if aid in peers:
                return aid
            lower = aid.lower()
            for key in peers:
                if key.lower() == lower or key.lower().startswith(lower) or key.lower().endswith(lower):
                    return key
            return aid

        agent_ids = [_resolve(aid) for aid in agent_ids]

        # Send a visible "consulting" message to the group
        if self._bus and ctx:
            channel, chat_id, metadata = ctx
            from nanobot.bus.events import OutboundMessage
            names = ", ".join(agent_ids)
            meta = dict(metadata)
            await self._bus.publish_outbound(OutboundMessage(
                channel=channel, chat_id=chat_id,
                content=f"💬 **[→ {names}]**\n\n{question}",
                metadata=meta,
            ))

        tasks: dict[str, asyncio.Task] = {}
        for aid in agent_ids:
            agent = peers.get(aid)
            if agent is None:
                continue

            # Build reply metadata using the peer's own account_id so the
            # response appears from the peer's Feishu account in the group.
            reply_channel = reply_chat_id = None
            reply_metadata: dict | None = None
            if ctx:
                channel, chat_id, metadata = ctx
                reply_channel = channel
                reply_chat_id = chat_id
                reply_metadata = dict(metadata)
                if self._get_agent_account:
                    peer_account = self._get_agent_account(channel, aid)
                    if peer_account:
                        reply_metadata["account_id"] = peer_account

            tasks[aid] = asyncio.create_task(
                agent.process_direct(
                    content=question,
                    session_key=f"discuss:{aid}:{chat_id if ctx else 'default'}",
                    channel=channel if ctx else "discuss",
                    chat_id=chat_id if ctx else "discuss",
                    reply_channel=reply_channel,
                    reply_chat_id=reply_chat_id,
                    reply_metadata=reply_metadata,
                )
            )
        if not tasks:
            return "No peer agents available to consult."

        results: list[str] = []
        for aid, task in tasks.items():
            try:
                response = await asyncio.wait_for(task, timeout=self.timeout)
                results.append(f"**{aid}**: {response}")
            except asyncio.TimeoutError:
                results.append(f"**{aid}**: (timed out after {self.timeout}s)")
                logger.warning("DiscussTool: agent '{}' timed out", aid)
            except Exception as e:
                results.append(f"**{aid}**: (error: {e})")
                logger.warning("DiscussTool: agent '{}' raised: {}", aid, e)
        return "\n\n".join(results)
