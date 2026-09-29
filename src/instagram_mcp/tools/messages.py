"""Message history tools for Instagram MCP Server.

Reading only, over the instagram-bridge. Sending/reacting/unsending live in the
channel tools, and new messages arrive as pushed channel events.
"""

from __future__ import annotations

import contextlib
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from instagram_mcp.bridge_client import BridgeClient

logger = logging.getLogger("instagram_mcp")


def _fmt_ts(iso: str | None, fmt: str) -> str:
    if not iso:
        return ""
    with contextlib.suppress(ValueError):
        return datetime.fromisoformat(iso).strftime(fmt)
    return iso


def register_message_tools(mcp: MCPServer, bridge: BridgeClient) -> None:
    """Register the message history tools with the MCP server."""

    @mcp.tool()
    def get_messages(thread_id: str, amount: int = 20, offset: int = 0) -> dict[str, Any]:
        """Get messages from a thread.

        Args:
            thread_id: ID of the thread to get messages from.
            amount: Maximum number of messages to fetch (default: 20).
            offset: Skip the N most recent messages (default: 0).

        Returns:
            Object with 'thread_id', 'messages' (sender, text, media_type,
            timestamp, seen_since when viewer's, share for a shared reel/post/story),
            'count', 'offset', 'has_more'.
        """
        try:
            fetch_total = offset + amount
            all_messages = bridge.messages(thread_id, amount=fetch_total)
        except Exception as e:
            logger.exception("Error getting messages for thread %s", thread_id)
            return {"error": str(e), "thread_id": thread_id}
        page = all_messages[offset : offset + amount]
        has_more = len(all_messages) >= fetch_total
        result_messages = []
        for m in page:
            d: dict[str, Any] = {
                "sender": m.get("username"),
                "text": m.get("text"),
                "media_type": m.get("media_type"),
                "timestamp": m.get("timestamp"),
            }
            if m.get("is_from_me"):
                d["seen_since"] = m.get("seen_since")
            if m.get("media_url"):
                d["media_url"] = m["media_url"]
            if m.get("link_url"):
                d["link_url"] = m["link_url"]
                d["link_title"] = m.get("link_title")
            if m.get("share"):
                d["share"] = m["share"]
            result_messages.append(d)
        return {
            "thread_id": thread_id,
            "messages": result_messages,
            "count": len(page),
            "offset": offset,
            "has_more": has_more,
        }

    @mcp.tool()
    def get_chat_log(thread_id: str, amount: int = 50, offset: int = 0) -> dict[str, Any]:
        """Get conversation as a readable chat log. Optimized for LLM analysis.

        Plain-text chronological log, ~5x smaller than JSON. Use get_messages for
        structured fields.

        Args:
            thread_id: ID of the thread to get messages from.
            amount: Maximum number of messages to fetch (default: 50).
            offset: Skip the N most recent messages (default: 0).

        Returns:
            Object with 'thread_id', 'log' (plain text), 'count', 'offset', 'has_more'.
        """
        try:
            fetch_total = offset + amount
            all_messages = bridge.messages(thread_id, amount=fetch_total)
        except Exception as e:
            logger.exception("Error getting chat log for thread %s", thread_id)
            return {"error": str(e), "thread_id": thread_id}
        page = all_messages[offset : offset + amount]
        has_more = len(all_messages) >= fetch_total
        chronological = [m for m in reversed(page) if m.get("media_type") != "action_log"]

        last_seen: int | None = None
        for m in page:  # newest-first
            if m.get("is_from_me") and m.get("seen_since") is not None:
                last_seen = m["seen_since"]
                break

        lines: list[str] = []
        for m in chronological:
            sender = "YOU" if m.get("is_from_me") else m.get("username")
            ts = _fmt_ts(m.get("timestamp"), "%b %d %H:%M")
            text = m.get("text") or f"[{m.get('media_type')}]"
            seen_tag = ""
            if m.get("is_from_me") and m is chronological[-1] and last_seen is not None:
                seen_tag = (
                    f" (seen {last_seen}m ago)"
                    if last_seen < 60
                    else f" (seen {last_seen // 60}h ago)"
                )
            lines.append(f"[{ts}] {sender}: {text}{seen_tag}")

        return {
            "thread_id": thread_id,
            "log": "\n".join(lines),
            "count": len(chronological),
            "offset": offset,
            "has_more": has_more,
        }
