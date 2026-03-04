"""DiscussTool — consult peer agents in the same group chat."""
from __future__ import annotations
import asyncio
from typing import TYPE_CHECKING, Any, Callable
from loguru import logger
from nanobot.agent.tools.base import Tool

if TYPE_CHECKING:
    from nanobot.agent.loop import AgentLoop


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
        timeout: float = 30.0,
    ):
        self._static_peers = peers or {}
        self._get_peers = get_peers
        self.timeout = timeout

    def _peers(self) -> dict[str, "AgentLoop"]:
        return self._get_peers() if self._get_peers is not None else self._static_peers

    async def execute(self, **kwargs: Any) -> str:
        question: str = kwargs["question"]
        agent_ids: list[str] = kwargs["agent_ids"]

        peers = self._peers()
        tasks: dict[str, asyncio.Task] = {}
        for aid in agent_ids:
            agent = peers.get(aid)
            if agent is None:
                continue
            tasks[aid] = asyncio.create_task(
                agent.process_direct(
                    content=question,
                    session_key=f"discuss:{aid}",
                    channel="discuss",
                    chat_id="discuss",
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
