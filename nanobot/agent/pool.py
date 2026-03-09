"""Agent pool for managing multiple agents."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Callable

from loguru import logger

from nanobot.agent.loop import AgentLoop
from nanobot.providers.fallback_provider import FallbackProvider
from nanobot.session.manager import SessionManager

if TYPE_CHECKING:
    from nanobot.bus.queue import MessageBus
    from nanobot.config.schema import Config
    from nanobot.providers.base import LLMProvider


class AgentPool:
    """Manages multiple AgentLoop instances and routes messages to them."""

    def __init__(
        self,
        config: Config,
        bus: MessageBus,
        provider_factory: Callable[[str, str | None], LLMProvider],
    ):
        self.config = config
        self.bus = bus
        self.provider_factory = provider_factory
        self._agents: dict[str, AgentLoop] = {}
        self._bindings: dict[tuple[str, str], str] = {}
        self._group_members: dict[str, set[str]] = {}  # chat_id -> {agent_id}

        self._init_agents()
        self._init_bindings()

    def _init_agents(self) -> None:
        """Create AgentLoop instances from config."""
        if not self.config.agents.list:
            # Backward compatibility: create default agent
            defaults = self.config.agents.defaults
            workspace = Path(defaults.workspace).expanduser()
            model = defaults.model
            fb_models = defaults.model_fallbacks
            img_model = defaults.image_model
            img_fb = defaults.image_model_fallbacks
            provider = self._build_provider_chain(model, fb_models)
            image_provider = self._build_provider_chain(img_model, img_fb) if img_model else None

            agent = self._create_agent(
                agent_id="default",
                model=provider.get_default_model(),
                provider=provider,
                workspace=workspace,
                temperature=defaults.temperature,
                max_tokens=defaults.max_tokens,
                max_iterations=defaults.max_tool_iterations,
                memory_window=defaults.memory_window,
                reasoning_effort=defaults.reasoning_effort,
                image_provider=image_provider,
            )
            self._agents["default"] = agent
            logger.info("Created default agent")
        else:
            # Create agents from list
            for agent_config in self.config.agents.list:
                defaults = self.config.agents.defaults
                model = agent_config.model or defaults.model
                provider_name = agent_config.provider or defaults.provider
                if provider_name == "auto":
                    provider_name = None
                fb_models = agent_config.model_fallbacks if agent_config.model_fallbacks is not None else defaults.model_fallbacks
                img_model = agent_config.image_model if agent_config.image_model is not None else defaults.image_model
                img_fb = agent_config.image_model_fallbacks if agent_config.image_model_fallbacks is not None else defaults.image_model_fallbacks
                provider = self._build_provider_chain(model, fb_models)
                image_provider = self._build_provider_chain(img_model, img_fb) if img_model else None
                # Use the provider's resolved model name (may differ, e.g. "anthropic/kimi-for-coding")
                resolved_model = provider.get_default_model()

                workspace_str = agent_config.workspace or defaults.workspace
                base = Path(workspace_str).expanduser()
                workspace = base.parent / f"{base.name}-{agent_config.id}"

                agent = self._create_agent(
                    agent_id=agent_config.id,
                    model=resolved_model,
                    provider=provider,
                    workspace=workspace,
                    temperature=agent_config.temperature or defaults.temperature,
                    max_tokens=agent_config.max_tokens or defaults.max_tokens,
                    max_iterations=agent_config.max_tool_iterations or defaults.max_tool_iterations,
                    memory_window=agent_config.memory_window or defaults.memory_window,
                    reasoning_effort=agent_config.reasoning_effort or defaults.reasoning_effort,
                    image_provider=image_provider,
                )
                self._agents[agent_config.id] = agent
                logger.info("Created agent: {}", agent_config.id)

    def _build_provider_chain(self, primary_model: str, fallback_models: list[str]) -> FallbackProvider:
        """Build a FallbackProvider from primary + fallback model list."""
        def _entry(model: str) -> tuple:
            p = self.provider_factory(model, None)
            # Use the provider's resolved default_model (may differ, e.g. "anthropic/kimi-for-coding")
            resolved = p.get_default_model()
            return (p, resolved)

        chain = [_entry(primary_model)]
        for fb in fallback_models:
            chain.append(_entry(fb))
        return FallbackProvider(chain)

    def _create_agent(
        self,
        agent_id: str,
        model: str,
        provider: LLMProvider,
        workspace: Path,
        temperature: float,
        max_tokens: int,
        max_iterations: int,
        memory_window: int,
        reasoning_effort: str | None,
        image_provider: "FallbackProvider | None" = None,
    ) -> AgentLoop:
        """Create a single AgentLoop instance."""
        workspace.mkdir(parents=True, exist_ok=True)
        self._init_workspace(workspace, agent_id)

        return AgentLoop(
            bus=self.bus,
            provider=provider,
            workspace=workspace,
            model=model,
            max_iterations=max_iterations,
            temperature=temperature,
            max_tokens=max_tokens,
            memory_window=memory_window,
            reasoning_effort=reasoning_effort,
            image_provider=image_provider,
            brave_api_key=self.config.tools.web.search.api_key,
            web_proxy=self.config.tools.web.proxy,
            exec_config=self.config.tools.exec,
            restrict_to_workspace=self.config.tools.restrict_to_workspace,
            session_manager=SessionManager(workspace),
            mcp_servers=self.config.tools.mcp_servers,
            channels_config=self.config.channels,
            openviking_config=self.config.tools.openviking,
            chrome_config=self.config.tools.chrome,
            agent_id=agent_id,
            agent_pool=self,
        )

    def _init_workspace(self, workspace: Path, agent_id: str) -> None:
        """Initialize workspace with default files if they don't exist yet."""
        defaults = {
            "AGENTS.md": f"""# Agent Instructions

You are a helpful AI assistant. Be concise, accurate, and friendly.

## Scheduled Reminders

Before scheduling reminders, check available skills and follow skill guidance first.
Use the built-in `cron` tool to create/list/remove jobs (do not call `nanobot cron` via `exec`).
Get USER_ID and CHANNEL from the current session (e.g., `8281248569` and `telegram` from `telegram:8281248569`).

**Do NOT just write reminders to MEMORY.md** — that won't trigger actual notifications.

## Heartbeat Tasks

`HEARTBEAT.md` is checked on the configured heartbeat interval. Use file tools to manage periodic tasks:

- **Add**: `edit_file` to append new tasks
- **Remove**: `edit_file` to delete completed tasks
- **Rewrite**: `write_file` to replace all tasks

When the user asks for a recurring/periodic task, update `HEARTBEAT.md` instead of creating a one-time cron reminder.
""",
            "SOUL.md": f"""# Soul

I am {agent_id}, a personal AI assistant.

## Personality

- Helpful and friendly
- Concise and to the point
- Curious and eager to learn

## Values

- Accuracy over speed
- User privacy and safety
- Transparency in actions

## Communication Style

- Be clear and direct
- Explain reasoning when helpful
- Ask clarifying questions when needed
""",
            "USER.md": """# User Profile

Information about the user to help personalize interactions.

## Basic Information

- **Name**: (your name)
- **Timezone**: (your timezone, e.g., UTC+8)
- **Language**: (preferred language)

## Preferences

- **Communication Style**: Casual / Professional / Technical
- **Response Length**: Brief / Detailed / Adaptive
- **Technical Level**: Beginner / Intermediate / Expert

## Work Context

- **Primary Role**: (your role, e.g., developer, researcher)
- **Main Projects**: (what you're working on)

## Special Instructions

(Any specific instructions for how the assistant should behave)
""",
            "TOOLS.md": """# Tool Usage Notes

Tool signatures are provided automatically via function calling.
This file documents non-obvious constraints and usage patterns.

## exec — Safety Limits

- Commands have a configurable timeout (default 60s)
- Dangerous commands are blocked (rm -rf, format, dd, shutdown, etc.)
- Output is truncated at 10,000 characters
- `restrictToWorkspace` config can limit file access to the workspace

## cron — Scheduled Reminders

- Please refer to cron skill for usage.
""",
        }
        for filename, content in defaults.items():
            path = workspace / filename
            if not path.exists():
                path.write_text(content, encoding="utf-8")
                logger.info("Initialized {} for agent {}", filename, agent_id)

    def register_group_member(self, chat_id: str, agent_id: str) -> None:
        """Record that agent_id is active in chat_id."""
        self._group_members.setdefault(chat_id, set()).add(agent_id)

    def get_agent_account(self, channel: str, agent_id: str) -> str | None:
        """Return the accountId bound to agent_id on the given channel, or None."""
        for (ch, account_id), ag_id in self._bindings.items():
            if ch == channel and ag_id == agent_id:
                return account_id
        return None

    def get_peer_agents(self, chat_id: str, exclude_agent_id: str) -> dict[str, "AgentLoop"]:
        """Return agent_id -> AgentLoop for all agents in chat_id except exclude_agent_id.

        Always includes all agents in the pool as candidates, since they are all
        configured peers. _group_members is used to track membership but does not
        restrict the candidate set — this ensures peers are visible even before
        they have spoken in the group.
        """
        candidates = set(self._agents.keys()) | self._group_members.get(chat_id, set())
        return {
            aid: self._agents[aid]
            for aid in candidates
            if aid != exclude_agent_id and aid in self._agents
        }

    def _init_bindings(self) -> None:
        """Build (channel, accountId) -> agentId map."""
        for binding in self.config.bindings:
            channel = binding.match.get("channel")
            account_id = binding.match.get("accountId")
            if channel and account_id:
                self._bindings[(channel, account_id)] = binding.agent_id

    def get_agent(self, channel: str, account_id: str) -> AgentLoop | None:
        """Get agent for the given channel and account_id."""
        agent_id = self._bindings.get((channel, account_id))
        if agent_id:
            return self._agents.get(agent_id)
        return None

    def get_default_agent(self) -> AgentLoop | None:
        """Get default agent for backward compatibility."""
        return self._agents.get("default")

    async def route_inbound(self, msg: "InboundMessage") -> None:
        """Route an inbound message to the correct agent based on account_id metadata."""
        from nanobot.bus.events import InboundMessage as _IM  # noqa: F401
        account_id = msg.metadata.get("account_id") if msg.metadata else None
        agent: AgentLoop | None = None
        if account_id:
            agent = self.get_agent(msg.channel, account_id)
        if agent is None:
            agent = self.get_default_agent()
        if agent is None and self._agents:
            agent = next(iter(self._agents.values()))
        if agent is not None:
            await agent.enqueue(msg)
        else:
            logger.warning("route_inbound: no agent available for message from {}", msg.channel)

    async def run_bus_router(self) -> None:
        """Consume the shared bus and route messages to per-agent queues (for non-Feishu channels)."""
        import asyncio as _asyncio
        while True:
            try:
                msg = await _asyncio.wait_for(self.bus.consume_inbound(), timeout=1.0)
            except _asyncio.TimeoutError:
                continue
            await self.route_inbound(msg)
