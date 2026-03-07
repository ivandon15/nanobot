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
            "This is the ONLY way to involve peer agents — do NOT use the `message` tool to @mention them. "
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

        channel: str | None = None
        chat_id: str | None = None
        metadata: dict = {}
        if ctx:
            channel, chat_id, metadata = ctx

        tasks: dict[str, asyncio.Task] = {}
        for aid in agent_ids:
            agent = peers.get(aid)
            if agent is None:
                continue

            # Resolve the Feishu account name for this peer (for @mention display)
            at_name = aid
            if self._get_agent_account and channel:
                account = self._get_agent_account(channel, aid)
                if account:
                    at_name = account

            # Send a visible "@AccountName question" message from the calling agent's account.
            # _build_post_with_mentions in feishu.py will convert this to a real Feishu @mention.
            if self._bus and channel and chat_id:
                from nanobot.bus.events import OutboundMessage
                await self._bus.publish_outbound(OutboundMessage(
                    channel=channel, chat_id=chat_id,
                    content=f"@{at_name} {question}",
                    metadata=dict(metadata),
                ))

            # Build reply metadata using the peer's own account_id so the
            # response appears from the peer's Feishu account in the group.
            reply_channel = reply_chat_id = None
            reply_metadata: dict | None = None
            inbound_metadata: dict = {}
            if channel and chat_id:
                reply_channel = channel
                reply_chat_id = chat_id
                reply_metadata = dict(metadata)
                peer_account = self._get_agent_account(channel, aid) if self._get_agent_account else None
                if peer_account:
                    reply_metadata["account_id"] = peer_account
                # Pass group context + peer's account_id so _process_message uses the right Feishu client
                inbound_metadata = {"chat_type": "group", "account_id": peer_account or aid}

            tasks[aid] = asyncio.create_task(
                agent.process_direct(
                    content=question,
                    session_key=f"discuss:{aid}:{chat_id or 'default'}",
                    channel=channel or "discuss",
                    chat_id=chat_id or "discuss",
                    reply_channel=reply_channel,
                    reply_chat_id=reply_chat_id,
                    reply_metadata=reply_metadata,
                    inbound_metadata=inbound_metadata,
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
