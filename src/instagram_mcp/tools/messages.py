"""Message history tools for Instagram MCP Server.

Reading only: sending, reacting and unsending live in the channel tools,
and new messages arrive as pushed channel events.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from instagram_mcp.models.schemas import MediaType

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from instagram_mcp.client import InstagramClient

logger = logging.getLogger("instagram_mcp")


def register_message_tools(mcp: MCPServer, client: InstagramClient) -> None:
    """Register the message history tools with the MCP server."""

    @mcp.tool()
    def get_messages(thread_id: str, amount: int = 20, offset: int = 0) -> dict[str, Any]:
        """Get messages from a thread.

        Args:
            thread_id: ID of the thread to get messages from.
            amount: Maximum number of messages to fetch (default: 20).
            offset: Skip the N most recent messages (default: 0).
                Use for pagination: offset=0 gets latest, offset=50 gets older.

        Returns:
            Object with 'thread_id', 'messages' array containing objects with
            sender, text, media_type, timestamp, and seen_since (when available).
        """
        try:
            fetch_total = offset + amount
            all_messages = client.get_messages(thread_id=thread_id, amount=fetch_total)
            page = all_messages[offset : offset + amount]
            has_more = len(all_messages) >= fetch_total

            result_messages = []
            for m in page:
                msg_dict: dict[str, Any] = {
                    "sender": m.sender.username,
                    "text": m.content.text,
                    "media_type": m.content.media_type.value,
                    "timestamp": m.timestamp.isoformat(),
                }
                if m.is_sent_by_viewer:
                    msg_dict["seen_since"] = m.seen_since
                if m.content.media_url is not None:
                    msg_dict["media_url"] = m.content.media_url
                result_messages.append(msg_dict)

            return {
                "thread_id": thread_id,
                "messages": result_messages,
                "count": len(page),
                "offset": offset,
                "has_more": has_more,
            }
        except Exception as e:
            logger.exception("Error getting messages for thread %s", thread_id)
            return {"error": str(e), "thread_id": thread_id}

    @mcp.tool()
    def get_chat_log(thread_id: str, amount: int = 50, offset: int = 0) -> dict[str, Any]:
        """Get conversation as a readable chat log. Optimized for LLM analysis.

        Returns messages as a plain-text chronological log, ~5x smaller than JSON.
        Use this for conversation analysis, reconnaissance, or context loading.
        Use get_messages for structured data when you need individual fields.

        Args:
            thread_id: ID of the thread to get messages from.
            amount: Maximum number of messages to fetch (default: 50).
            offset: Skip the N most recent messages (default: 0).
                Use for pagination: offset=0 gets latest, offset=150 gets older.

        Returns:
            Object with 'thread_id', 'thread_title', 'log' (plain text),
            'count', 'offset', and 'has_more'.
        """
        try:
            fetch_total = offset + amount
            all_messages = client.get_messages(thread_id=thread_id, amount=fetch_total)
            page = all_messages[offset : offset + amount]
            has_more = len(all_messages) >= fetch_total

            # Reverse to chronological order (oldest first)
            chronological = list(reversed(page))

            # Find the last viewer message for seen_since annotation
            last_viewer_seen_since = None
            for m in page:  # page is newest-first
                if m.is_sent_by_viewer and m.seen_since is not None:
                    last_viewer_seen_since = m.seen_since
                    break

            lines: list[str] = []
            for m in chronological:
                if m.content.media_type == MediaType.ACTION_LOG:
                    continue

                sender = "YOU" if m.is_sent_by_viewer else m.sender.username
                ts = m.timestamp.strftime("%b %d %H:%M")

                text = m.content.text or f"[{m.content.media_type.value}]"

                seen_tag = ""
                if (
                    m.is_sent_by_viewer
                    and m == chronological[-1]
                    and last_viewer_seen_since is not None
                ):
                    if last_viewer_seen_since < 60:
                        seen_tag = f" (seen {last_viewer_seen_since}m ago)"
                    else:
                        hours = last_viewer_seen_since // 60
                        seen_tag = f" (seen {hours}h ago)"

                lines.append(f"[{ts}] {sender}: {text}{seen_tag}")

            log_text = "\n".join(lines)

            return {
                "thread_id": thread_id,
                "log": log_text,
                "count": len(
                    [m for m in chronological if m.content.media_type != MediaType.ACTION_LOG]
                ),
                "offset": offset,
                "has_more": has_more,
            }
        except Exception as e:
            logger.exception("Error getting chat log for thread %s", thread_id)
            return {"error": str(e), "thread_id": thread_id}

