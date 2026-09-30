"""Thread management tools for Instagram MCP Server.

Thin client: these call the instagram-bridge over HTTP; the bridge owns the one
Instagram login. They list/search threads and change thread state.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from mcp.types import ToolAnnotations

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from instagram_mcp.bridge_client import BridgeClient

logger = logging.getLogger("instagram_mcp")


def _title(t: dict[str, Any]) -> str:
    return t.get("thread_title") or ", ".join(u["username"] for u in t.get("users", []))


def register_thread_tools(mcp: MCPServer, bridge: BridgeClient) -> None:
    """Register thread management tools with the MCP server."""

    @mcp.tool()
    def list_threads(amount: int = 20) -> dict[str, Any]:
        """Get direct message threads from inbox.

        Args:
            amount: Maximum number of threads to fetch (default: 20).

        Returns:
            Object with 'threads' array (thread_id, thread_title, users, is_group,
            is_muted, unread, last_activity_at) and 'count'.
        """
        try:
            threads = bridge.threads(amount=amount)
        except Exception as e:
            logger.exception("Error listing threads")
            return {"error": str(e)}
        for t in threads:
            t["thread_title"] = _title(t)
        return {"threads": threads, "count": len(threads)}

    @mcp.tool()
    def get_thread(thread_id: str, amount: int = 20) -> dict[str, Any]:
        """Get a specific thread with its messages.

        Args:
            thread_id: ID of the thread to fetch.
            amount: Maximum number of messages to fetch (default: 20).

        Returns:
            Thread object with thread_id, thread_title, users, is_group, and a
            messages array (message_id, sender, text, media_type, timestamp, ...).
        """
        try:
            t = bridge.thread(thread_id, amount=amount)
        except Exception as e:
            logger.exception("Error getting a thread")
            return {"error": str(e)}
        if "error" in t:
            return t
        t["thread_title"] = _title(t)
        return t

    @mcp.tool()
    def search_threads(query: str) -> dict[str, Any]:
        """Search threads by username or title.

        Args:
            query: Search query (username or thread title).

        Returns:
            Object with 'query', 'threads' array of matches, and 'count'.
        """
        try:
            threads = bridge.search(query)
        except Exception as e:
            logger.exception("Error searching threads")
            return {"error": str(e)}
        for t in threads:
            t["thread_title"] = _title(t)
        return {"query": query, "threads": threads, "count": len(threads)}

    @mcp.tool()
    def get_pending_threads() -> dict[str, Any]:
        """Get pending message request threads.

        Returns:
            Object with 'threads' array of pending threads and 'count'.
        """
        try:
            threads = bridge.pending()
        except Exception as e:
            logger.exception("Error getting pending threads")
            return {"error": str(e)}
        for t in threads:
            t["thread_title"] = _title(t)
        return {"threads": threads, "count": len(threads)}

    @mcp.tool(annotations=ToolAnnotations(destructive_hint=True))
    def hide_thread(thread_id: str) -> dict[str, Any]:
        """Hide/delete a thread from inbox.

        Args:
            thread_id: ID of the thread to hide.
        """
        try:
            return {**bridge.hide(thread_id), "thread_id": thread_id}
        except Exception as e:
            logger.exception("Error hiding a thread")
            return {"error": str(e), "thread_id": thread_id}

    @mcp.tool()
    def mark_thread_unread(thread_id: str) -> dict[str, Any]:
        """Mark a thread as unread.

        Args:
            thread_id: ID of the thread to mark unread.
        """
        try:
            return {**bridge.mark_unread(thread_id), "thread_id": thread_id}
        except Exception as e:
            logger.exception("Error marking a thread unread")
            return {"error": str(e), "thread_id": thread_id}

    @mcp.tool()
    def mute_thread(thread_id: str) -> dict[str, Any]:
        """Mute notifications for a thread.

        Args:
            thread_id: ID of the thread to mute.
        """
        try:
            return {**bridge.mute(thread_id), "thread_id": thread_id}
        except Exception as e:
            logger.exception("Error muting a thread")
            return {"error": str(e), "thread_id": thread_id}

    @mcp.tool()
    def unmute_thread(thread_id: str) -> dict[str, Any]:
        """Unmute notifications for a thread.

        Args:
            thread_id: ID of the thread to unmute.
        """
        try:
            return {**bridge.unmute(thread_id), "thread_id": thread_id}
        except Exception as e:
            logger.exception("Error unmuting a thread")
            return {"error": str(e), "thread_id": thread_id}
