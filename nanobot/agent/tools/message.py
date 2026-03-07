"""Message tool for sending messages to users."""

import re
from typing import Any, Awaitable, Callable

from nanobot.agent.tools.base import Tool
from nanobot.bus.events import OutboundMessage


class MessageTool(Tool):
    """Tool to send messages to users on chat channels."""

    def __init__(
        self,
        send_callback: Callable[[OutboundMessage], Awaitable[None]] | None = None,
        default_channel: str = "",
        default_chat_id: str = "",
        default_message_id: str | None = None,
        default_metadata: dict | None = None,
        get_peer_names: Callable[[], list[str]] | None = None,
    ):
        self._send_callback = send_callback
        self._default_channel = default_channel
        self._default_chat_id = default_chat_id
        self._default_message_id = default_message_id
        self._default_metadata: dict = default_metadata or {}
        self._sent_in_turn: bool = False
        self._last_content: str | None = None
        self._get_peer_names = get_peer_names

    def set_context(self, channel: str, chat_id: str, message_id: str | None = None, metadata: dict | None = None) -> None:
        """Set the current message context."""
        self._default_channel = channel
        self._default_chat_id = chat_id
        self._default_message_id = message_id
        self._default_metadata = metadata or {}

    def set_send_callback(self, callback: Callable[[OutboundMessage], Awaitable[None]]) -> None:
        """Set the callback for sending messages."""
        self._send_callback = callback

    def start_turn(self) -> None:
        """Reset per-turn send tracking."""
        self._sent_in_turn = False
        self._last_content = None

    @property
    def name(self) -> str:
        return "message"

    @property
    def description(self) -> str:
        peer_names = self._get_peer_names() if self._get_peer_names else []
        base = "Send a message to the user. Use this when you want to communicate something."
        if peer_names:
            names = ", ".join(peer_names)
            base += (
                f" WARNING: Do NOT use this to contact peer agents ({names}). "
                "Use `discuss_with_agents` instead — @mentioning peers via this tool does NOT work."
            )
        return base

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The message content to send"
                },
                "channel": {
                    "type": "string",
                    "description": "Optional: target channel (telegram, discord, etc.)"
                },
                "chat_id": {
                    "type": "string",
                    "description": "Optional: target chat/user ID"
                },
                "media": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional: list of file paths to attach (images, audio, documents)"
                }
            },
            "required": ["content"]
        }

    async def execute(
        self,
        content: str,
        channel: str | None = None,
        chat_id: str | None = None,
        message_id: str | None = None,
        media: list[str] | None = None,
        **kwargs: Any
    ) -> str:
        channel = channel or self._default_channel
        chat_id = chat_id or self._default_chat_id
        message_id = message_id or self._default_message_id

        if not channel or not chat_id:
            return "Error: No target channel/chat specified"

        if not self._send_callback:
            return "Error: Message sending not configured"

        # Guard: detect @mention of a peer agent and redirect to discuss_with_agents
        if self._get_peer_names:
            peer_names = self._get_peer_names()
            for name in peer_names:
                if re.search(rf"@{re.escape(name)}\b", content, re.IGNORECASE):
                    return (
                        f"Error: @mentioning peer agent '{name}' via the message tool does not work — "
                        f"peers do not receive messages sent this way. "
                        f"Use `discuss_with_agents` with agent_id=\"{name}\" instead."
                    )

        msg = OutboundMessage(
            channel=channel,
            chat_id=chat_id,
            content=content,
            media=media or [],
            metadata={
                **self._default_metadata,
                "message_id": message_id,
            }
        )

        try:
            await self._send_callback(msg)
            if channel == self._default_channel and chat_id == self._default_chat_id:
                self._sent_in_turn = True
                self._last_content = content
            media_info = f" with {len(media)} attachments" if media else ""
            return f"Message sent to {channel}:{chat_id}{media_info}"
        except Exception as e:
            return f"Error sending message: {str(e)}"
