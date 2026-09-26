"""Channel tools: the WhatsApp-style tool surface.

Subscription tools decide which chats stream events into the session; the
messaging tools address those chats by alias.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mcp.types import ToolAnnotations

from instagram_mcp.channel import ChannelError

if TYPE_CHECKING:
    from collections.abc import Callable

    from mcp.server.mcpserver import MCPServer

    from instagram_mcp.channel import Channel
    from instagram_mcp.client import InstagramClient
    from instagram_mcp.mqtt.manager import MQTTManager

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


_PHOTO_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
_VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v"}


def register_messaging_tools(
    mcp: MCPServer,
    client: InstagramClient,
    channel: Channel,
    mqtt: Callable[[], MQTTManager | None],
    media_dir: Path,
) -> None:
    """Register the WhatsApp-style messaging tools with the MCP server."""

    def target(to: str | None) -> str:
        return channel.resolve(to)

    @mcp.tool()
    def reply(text: str, to: str | None = None) -> dict[str, Any]:
        """Send a text message. Address by alias via `to`, or omit it for the sole target.

        Args:
            text: Message text.
            to: Chat alias (e.g. "ly"). Omit for the sole subscribed target.
        """
        try:
            thread_id = target(to)
            channel.expect_echo(thread_id, "text", text)
            message = client.reply_to_thread(thread_id=thread_id, text=text)
        except ChannelError as e:
            return _refused(e)
        except Exception as e:
            logger.exception("reply failed")
            return {"success": False, "error": str(e)}
        if message is None:
            return {"success": False, "error": "Instagram did not confirm the send"}
        return {
            "success": True,
            "sent": channel.display(thread_id),
            "message_id": message.message_id,
        }

    @mcp.tool()
    def send_file(file_path: str, to: str | None = None) -> dict[str, Any]:
        """Send a photo or video (Instagram DMs take no other file types).

        Args:
            file_path: Absolute path to the photo or video.
            to: Chat alias. Omit for the sole subscribed target.
        """
        path = Path(file_path).expanduser()
        suffix = path.suffix.lower()
        if not path.is_file():
            return {"success": False, "error": f"no such file: {path}"}
        if suffix not in _PHOTO_SUFFIXES | _VIDEO_SUFFIXES:
            return {
                "success": False,
                "error": f"Instagram DMs only take photos and videos, not {suffix}",
            }
        try:
            thread_id = target(to)
            channel.expect_echo(thread_id, "media")
            send = client.send_photo if suffix in _PHOTO_SUFFIXES else client.send_video
            message = send(path=path, thread_ids=[thread_id])
        except ChannelError as e:
            return _refused(e)
        except Exception as e:
            logger.exception("send_file failed")
            return {"success": False, "error": str(e)}
        if message is None:
            return {"success": False, "error": "Instagram did not confirm the send"}
        return {
            "success": True,
            "sent": channel.display(thread_id),
            "message_id": message.message_id,
        }

    @mcp.tool()
    def send_audio(file_path: str, to: str | None = None) -> dict[str, Any]:
        """Send an audio file as a voice message (converted to .m4a if needed).

        Args:
            file_path: Absolute path to the audio file.
            to: Chat alias. Omit for the sole subscribed target.
        """
        path = Path(file_path).expanduser()
        if not path.is_file():
            return {"success": False, "error": f"no such file: {path}"}
        try:
            thread_id = target(to)
            channel.expect_echo(thread_id, "media")
            message = client.send_voice(path, thread_id)
        except ChannelError as e:
            return _refused(e)
        except Exception as e:
            logger.exception("send_audio failed")
            return {"success": False, "error": str(e)}
        if message is None:
            return {"success": False, "error": "Instagram did not confirm the send"}
        return {
            "success": True,
            "sent": channel.display(thread_id),
            "message_id": message.message_id,
        }

    @mcp.tool()
    def send_typing(to: str | None = None, composing: bool = True) -> dict[str, Any]:
        """Optional typing indicator: they see "typing…" while composing is true.

        Args:
            to: Chat alias. Omit for the sole subscribed target.
            composing: True starts the indicator, false stops it.
        """
        manager = mqtt()
        if manager is None:
            return {"success": False, "error": "no realtime connection this session"}
        try:
            manager.indicate_activity(target(to), active=composing)
        except ChannelError as e:
            return _refused(e)
        except Exception as e:
            return {"success": False, "error": str(e)}
        return {"success": True}

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
            thread_id = target(to)
            newest = max(message_ids, key=lambda i: int(i) if i.isdigit() else 0)
            ok = client.mark_seen(thread_id, newest)
        except ChannelError as e:
            return _refused(e)
        except Exception as e:
            return {"success": False, "error": str(e)}
        return {"success": ok}

    @mcp.tool()
    def download_attachment(message_id: str, to: str | None = None) -> dict[str, Any]:
        """Download a message's photo, video or voice clip and return the local path.

        View-once media can't be downloaded.

        Args:
            message_id: Message id from the channel event.
            to: Chat alias. Omit for the sole subscribed target.
        """
        try:
            path = client.download_message_media(target(to), message_id, media_dir)
        except ChannelError as e:
            return _refused(e)
        except Exception as e:
            return {"success": False, "error": str(e)}
        return {"success": True, "path": str(path.resolve())}

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
            thread_id = target(to)
            recent = client.get_messages(thread_id=thread_id, amount=max(limit * 3, 20))
        except ChannelError as e:
            return _refused(e)
        except Exception as e:
            return {"success": False, "error": str(e)}
        mine = [
            m
            for m in recent
            if m.is_sent_by_viewer and (not filter or filter in (m.content.text or ""))
        ][:limit]
        lines = []
        for m in mine:
            text = m.content.text or f"[{m.content.media_type.value}]"
            lines.append(f"{m.message_id} | {m.timestamp:%H:%M} | {text[:80]}")
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
            ok = client.delete_message(thread_id=thread_id, message_id=message_id)
        except ChannelError as e:
            return _refused(e)
        except Exception as e:
            return {"success": False, "error": str(e)}
        return {"success": ok, "unsent": channel.display(thread_id)}

    @mcp.tool()
    def react(message_id: str, emoji: str, to: str | None = None) -> dict[str, Any]:
        """React to a message with an emoji. An empty emoji removes your reaction.

        Args:
            message_id: Message id (from a channel event or get_message_ids).
            emoji: E.g. "❤️"; "" removes your reaction.
            to: Chat alias. Omit for the sole subscribed target.
        """
        try:
            thread_id = target(to)
            remove = emoji == ""
            current = channel.own_reaction(thread_id, message_id) or "❤️"
            channel.expect_echo(thread_id, "reaction", message_id)
            ok = client.react(thread_id, message_id, current if remove else emoji, remove=remove)
        except ChannelError as e:
            return _refused(e)
        except Exception as e:
            return {"success": False, "error": str(e)}
        if ok:
            channel.remember_reaction(thread_id, message_id, emoji)
        return {"success": ok}
