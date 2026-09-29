"""Channel tools: the WhatsApp-style tool surface.

Subscription tools decide which chats stream events into the session; the
messaging tools address those chats by alias.
"""

from __future__ import annotations

import contextlib
import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from mcp.types import ToolAnnotations

from instagram_mcp.channel import ChannelError
from instagram_mcp.video import VIDEO_SUFFIXES

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from instagram_mcp.bridge_client import BridgeClient
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
            chat_id: Thread id, e.g. '340282366841700000000000000000000000001'.
            alias: Short memorable handle (e.g. 'alex'). Derived from the thread
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


_PHOTO_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".heic"}


def register_messaging_tools(mcp: MCPServer, bridge: BridgeClient, channel: Channel) -> None:
    """Register the WhatsApp-style messaging tools (all routed through the bridge)."""

    def target(to: str | None) -> str:
        return channel.resolve(to)

    def _sent(thread_id: str, result: dict[str, Any]) -> dict[str, Any]:
        if not result.get("success"):
            return {"success": False, "error": result.get("error", "not confirmed")}
        return {
            "success": True,
            "sent": channel.display(thread_id),
            "message_id": result.get("message_id"),
        }

    @mcp.tool()
    def reply(text: str, to: str | None = None) -> dict[str, Any]:
        """Send a text message. Address by alias via `to`, or omit it for the sole target.

        Args:
            text: Message text.
            to: Chat alias (e.g. "alex"). Omit for the sole subscribed target.
        """
        try:
            thread_id = target(to)
            channel.expect_echo(thread_id, "text", text)
            return _sent(thread_id, bridge.send(thread_id, text))
        except ChannelError as e:
            return _refused(e)
        except Exception as e:
            logger.exception("reply failed")
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def send_file(
        file_path: str,
        to: str | None = None,
        view_mode: Literal["once", "replayable"] | None = None,
    ) -> dict[str, Any]:
        """Send a photo or video (Instagram DMs take no other file types).

        The path is read on the bridge host (where Instagram is connected).

        Args:
            file_path: Path to the photo or video (on the bridge host).
            to: Chat alias. Omit for the sole subscribed target.
            view_mode: "once" (view once) or "replayable" (allow replay) sends it as a
                disappearing photo/video (videos: H.264 mp4). Omit for a normal one.
        """
        suffix = Path(file_path).suffix.lower()
        if suffix not in _PHOTO_SUFFIXES | VIDEO_SUFFIXES:
            return {
                "success": False,
                "error": f"Instagram DMs only take photos and videos, not {suffix}",
            }
        kind = "video" if suffix in VIDEO_SUFFIXES else "photo"
        try:
            thread_id = target(to)
            channel.expect_echo(thread_id, "media")
            return _sent(thread_id, bridge.send_media(thread_id, file_path, kind, view_mode))
        except ChannelError as e:
            return _refused(e)
        except Exception as e:
            logger.exception("send_file failed")
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def send_audio(file_path: str, to: str | None = None) -> dict[str, Any]:
        """Send an audio file as a voice message (bridge converts to .m4a if needed).

        The path is read on the bridge host.

        Args:
            file_path: Path to the audio file (on the bridge host).
            to: Chat alias. Omit for the sole subscribed target.
        """
        try:
            thread_id = target(to)
            channel.expect_echo(thread_id, "media")
            return _sent(thread_id, bridge.send_voice(thread_id, file_path))
        except ChannelError as e:
            return _refused(e)
        except Exception as e:
            logger.exception("send_audio failed")
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def send_typing(to: str | None = None, composing: bool = True) -> dict[str, Any]:
        """Optional typing indicator: they see "typing..." while composing is true.

        Args:
            to: Chat alias. Omit for the sole subscribed target.
            composing: True starts the indicator, false stops it.
        """
        try:
            return bridge.typing(target(to), active=composing)
        except ChannelError as e:
            return _refused(e)
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def mark_read(message_ids: list[str], to: str | None = None) -> dict[str, Any]:
        """Optional read receipt ("Seen") up to the newest of these messages.

        Args:
            message_ids: Message ids from channel events; the newest one is used.
            to: Chat alias. Omit for the sole subscribed target.
        """
        if not message_ids:
            return {"success": False, "error": "no message ids"}
        try:
            newest = max(message_ids, key=lambda i: int(i) if i.isdigit() else 0)
            return bridge.mark_read(target(to), newest)
        except ChannelError as e:
            return _refused(e)
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def download_attachment(message_id: str, to: str | None = None) -> dict[str, Any]:
        """Download a message's photo, video or voice clip; returns the path on the bridge host.

        View-once media lands in a temporary folder and is deleted soon; never keep it.

        Args:
            message_id: Message id from the channel event.
            to: Chat alias. Omit for the sole subscribed target.
        """
        try:
            return bridge.download(target(to), message_id)
        except ChannelError as e:
            return _refused(e)
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def open_share(
        message_id: str, to: str | None = None, transcribe: bool = True
    ) -> dict[str, Any]:
        """Open a shared reel, post or story: download it to see and hear what's in it.

        Returns the caption, the downloaded photos/videos, one frame-strip image per
        video (Read it to see what happens) and what is said in it.

        Args:
            message_id: Message id of the share, from the channel event.
            to: Chat alias. Omit for the sole subscribed target.
            transcribe: Transcribe the audio too; False for music-only reels.
        """
        try:
            return bridge.open_share(target(to), message_id, transcribe=transcribe)
        except ChannelError as e:
            return _refused(e)
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def get_message_ids(
        to: str | None = None, filter: str | None = None, limit: int = 10
    ) -> dict[str, Any]:
        """List your OWN recent messages with their ids, newest first (for unsend).

        Args:
            to: Chat alias. Omit for the sole subscribed target.
            filter: Only messages whose text contains this substring.
            limit: How many to return (default 10).
        """
        try:
            recent = bridge.messages(target(to), amount=max(limit * 3, 20))
        except ChannelError as e:
            return _refused(e)
        except Exception as e:
            return {"success": False, "error": str(e)}
        mine = [
            m
            for m in recent
            if m.get("is_from_me") and (not filter or filter in (m.get("text") or ""))
        ][:limit]
        lines = []
        for m in mine:
            text = m.get("text") or f"[{m.get('media_type')}]"
            hhmm = ""
            with contextlib.suppress(ValueError, TypeError):
                hhmm = datetime.fromisoformat(m["timestamp"]).strftime("%H:%M")
            lines.append(f"{m.get('message_id')} | {hhmm} | {text[:80]}")
        return {"messages": lines}

    @mcp.tool(annotations=ToolAnnotations(destructive_hint=True))
    def unsend(message_id: str, to: str | None = None) -> dict[str, Any]:
        """Unsend one of YOUR messages for everyone. Get the id from get_message_ids.

        Args:
            message_id: Id of your message.
            to: Chat alias. Omit for the sole subscribed target.
        """
        try:
            thread_id = target(to)
            channel.expect_echo(thread_id, "unsend", message_id)
            result = bridge.unsend(thread_id, message_id)
        except ChannelError as e:
            return _refused(e)
        except Exception as e:
            return {"success": False, "error": str(e)}
        return {"success": result.get("success", False), "unsent": channel.display(thread_id)}

    @mcp.tool()
    def react(message_id: str, emoji: str, to: str | None = None) -> dict[str, Any]:
        """React to a message with an emoji. An empty emoji removes your reaction.

        Args:
            message_id: Message id (from a channel event or get_message_ids).
            emoji: E.g. "\u2764\ufe0f"; "" removes your reaction.
            to: Chat alias. Omit for the sole subscribed target.
        """
        try:
            thread_id = target(to)
            remove = emoji == ""
            current = channel.own_reaction(thread_id, message_id) or "\u2764\ufe0f"
            channel.expect_echo(thread_id, "reaction", message_id)
            result = bridge.react(
                thread_id, message_id, current if remove else emoji, remove=remove
            )
        except ChannelError as e:
            return _refused(e)
        except Exception as e:
            return {"success": False, "error": str(e)}
        ok = result.get("success", False)
        if ok:
            channel.remember_reaction(thread_id, message_id, emoji)
        return {"success": ok}
