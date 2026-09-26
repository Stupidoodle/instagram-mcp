"""Channel tools: manage which chats stream events into the session.

The messaging tools that go with them live in the same module so the whole
WhatsApp-style tool surface is registered in one place.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from instagram_mcp.channel import ChannelError

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from instagram_mcp.channel import Channel

logger = logging.getLogger("instagram_mcp")


def _refused(error: Exception) -> dict[str, Any]:
    return {"success": False, "error": f"refused: {error}"}


def register_channel_tools(mcp: MCPServer, channel: Channel) -> None:
    """Register the subscription tools with the MCP server."""

    @mcp.tool()
    def subscribe(chat_id: str, alias: str | None = None) -> dict[str, Any]:
        """Subscribe to an Instagram thread and give it a short alias.

        This is the ONE place a raw thread id is entered (from list_threads or
        search_threads). Only subscribed chats deliver events.

        Args:
            chat_id: Thread id, e.g. '340282366841710300949128531777654287254'.
            alias: Short memorable handle (e.g. 'ly'). Derived from the thread
                title if omitted.
        """
        try:
            final = channel.subscribe(chat_id, alias)
        except ChannelError as e:
            return _refused(e)
        return {"success": True, "subscribed": final, "chat": channel.display(chat_id)}

    @mcp.tool()
    def unsubscribe(to: str) -> dict[str, Any]:
        """Unsubscribe from a chat. Stops delivering its events.

        Args:
            to: Alias or thread id to unsubscribe.
        """
        try:
            alias = channel.unsubscribe(to)
        except ChannelError as e:
            return _refused(e)
        return {"success": True, "unsubscribed": alias}

    @mcp.tool()
    def list_subscriptions() -> dict[str, Any]:
        """List subscribed chats and their aliases."""
        return {"subscriptions": channel.subscriptions()}

    @mcp.tool()
    def set_idle(minutes: float, to: str | None = None) -> dict[str, Any]:
        """Tune how long a chat must be quiet before you get an [idle] nudge.

        Ramp it up to back off (60 = hourly while they're gone) or 0 to pause.
        Resets to the default the moment they next message.

        Args:
            minutes: Quiet-time threshold in minutes (0 pauses nudges).
            to: Chat alias. Omit for the sole subscribed target.
        """
        if minutes < 0:
            return _refused(ValueError("minutes must be >= 0"))
        try:
            thread_id = channel.resolve(to)
            channel.set_idle(thread_id, minutes)
        except ChannelError as e:
            return _refused(e)
        state = "paused" if minutes == 0 else f"{minutes:g}m"
        reset = f"resets to {channel.idle_minutes:g}m when they write"
        return {"success": True, "idle": f"{state} for {channel.display(thread_id)} ({reset})"}
