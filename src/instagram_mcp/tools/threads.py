"""Thread management tools for Instagram MCP Server.

This module provides MCP tools for managing Instagram Direct Message threads,
including listing, searching, and modifying thread states.
"""

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from instagram_mcp.client import InstagramClient

logger = logging.getLogger("instagram_mcp")


def register_thread_tools(mcp: "FastMCP", client: "InstagramClient") -> None:
    """Register thread management tools with the MCP server.

    Args:
        mcp: FastMCP server instance.
        client: Instagram client instance.
    """

    @mcp.tool()
    def list_threads(amount: int = 20) -> dict[str, Any]:
        """Get direct message threads from inbox.

        Args:
            amount: Maximum number of threads to fetch (default: 20).

        Returns:
            Object with 'threads' array containing thread objects with fields:
            thread_id, thread_title, users, is_group, is_muted, unread.
        """
        try:
            threads = client.get_threads(amount=amount)
            return {
                "threads": [
                    {
                        "thread_id": t.thread_id,
                        "thread_title": t.thread_title or ", ".join(u.username for u in t.users),
                        "users": [{"user_id": u.user_id, "username": u.username} for u in t.users],
                        "is_group": t.is_group,
                        "is_muted": t.is_muted,
                        "unread": t.unread,
                        "last_activity_at": (
                            t.last_activity_at.isoformat() if t.last_activity_at else None
                        ),
                    }
                    for t in threads
                ],
                "count": len(threads),
            }
        except Exception as e:
            logger.exception("Error listing threads")
            return {"error": str(e)}

    @mcp.tool()
    def get_thread(thread_id: str, amount: int = 20) -> dict[str, Any]:
        """Get a specific thread with its messages.

        Args:
            thread_id: ID of the thread to fetch.
            amount: Maximum number of messages to fetch (default: 20).

        Returns:
            Thread object with fields: thread_id, thread_title, users, is_group,
            is_muted, unread, and messages array with sender, content, timestamp.
        """
        try:
            thread = client.get_thread(thread_id=thread_id, amount=amount)
            return {
                "thread_id": thread.thread_id,
                "thread_title": thread.thread_title or ", ".join(u.username for u in thread.users),
                "users": [
                    {"user_id": u.user_id, "username": u.username, "full_name": u.full_name}
                    for u in thread.users
                ],
                "is_group": thread.is_group,
                "is_muted": thread.is_muted,
                "unread": thread.unread,
                "messages": [
                    {
                        "message_id": m.message_id,
                        "sender": m.sender.username,
                        "sender_id": m.sender.user_id,
                        "text": m.content.text,
                        "media_type": m.content.media_type.value,
                        "media_url": m.content.media_url,
                        "timestamp": m.timestamp.isoformat(),
                        "is_sent_by_viewer": m.is_sent_by_viewer,
                        "seen_since": m.seen_since,
                        "reactions": (
                            {
                                "emojis": [
                                    {
                                        "emoji": r.emoji,
                                        "sender_id": r.sender_id,
                                        "timestamp": r.timestamp.isoformat(),
                                    }
                                    for r in m.content.reactions.emojis
                                ]
                            }
                            if m.content.reactions and m.content.reactions.emojis
                            else None
                        ),
                    }
                    for m in thread.messages
                ],
            }
        except Exception as e:
            logger.exception("Error getting thread %s", thread_id)
            return {"error": str(e)}

    @mcp.tool()
    def search_threads(query: str) -> dict[str, Any]:
        """Search threads by username or title.

        Args:
            query: Search query (username or thread title).

        Returns:
            Object with 'threads' array of matching threads and 'count'.
        """
        try:
            threads = client.search_threads(query=query)
            return {
                "query": query,
                "threads": [
                    {
                        "thread_id": t.thread_id,
                        "thread_title": t.thread_title or ", ".join(u.username for u in t.users),
                        "users": [{"user_id": u.user_id, "username": u.username} for u in t.users],
                    }
                    for t in threads
                ],
                "count": len(threads),
            }
        except Exception as e:
            logger.exception("Error searching threads")
            return {"error": str(e)}

    @mcp.tool()
    def get_pending_threads() -> dict[str, Any]:
        """Get pending message request threads.

        Returns:
            Object with 'threads' array of pending threads awaiting approval.
        """
        try:
            threads = client.get_pending_threads()
            return {
                "threads": [
                    {
                        "thread_id": t.thread_id,
                        "thread_title": t.thread_title or ", ".join(u.username for u in t.users),
                        "users": [{"user_id": u.user_id, "username": u.username} for u in t.users],
                    }
                    for t in threads
                ],
                "count": len(threads),
            }
        except Exception as e:
            logger.exception("Error getting pending threads")
            return {"error": str(e)}

    @mcp.tool(annotations={"destructive": True})
    def hide_thread(thread_id: str) -> dict[str, Any]:
        """Hide/delete a thread from inbox.

        Args:
            thread_id: ID of the thread to hide.

        Returns:
            Object with 'success' boolean and 'thread_id'.
        """
        try:
            success = client.hide_thread(thread_id=thread_id)
            return {"success": success, "thread_id": thread_id}
        except Exception as e:
            logger.exception("Error hiding thread %s", thread_id)
            return {"error": str(e), "thread_id": thread_id}

    @mcp.tool()
    def mark_thread_unread(thread_id: str) -> dict[str, Any]:
        """Mark a thread as unread.

        Args:
            thread_id: ID of the thread to mark as unread.

        Returns:
            Object with 'success' boolean and 'thread_id'.
        """
        try:
            success = client.mark_thread_unread(thread_id=thread_id)
            return {"success": success, "thread_id": thread_id}
        except Exception as e:
            logger.exception("Error marking thread unread %s", thread_id)
            return {"error": str(e), "thread_id": thread_id}

    @mcp.tool()
    def mute_thread(thread_id: str) -> dict[str, Any]:
        """Mute notifications for a thread.

        Args:
            thread_id: ID of the thread to mute.

        Returns:
            Object with 'success' boolean and 'thread_id'.
        """
        try:
            success = client.mute_thread(thread_id=thread_id)
            return {"success": success, "thread_id": thread_id}
        except Exception as e:
            logger.exception("Error muting thread %s", thread_id)
            return {"error": str(e), "thread_id": thread_id}

    @mcp.tool()
    def unmute_thread(thread_id: str) -> dict[str, Any]:
        """Unmute notifications for a thread.

        Args:
            thread_id: ID of the thread to unmute.

        Returns:
            Object with 'success' boolean and 'thread_id'.
        """
        try:
            success = client.unmute_thread(thread_id=thread_id)
            return {"success": success, "thread_id": thread_id}
        except Exception as e:
            logger.exception("Error unmuting thread %s", thread_id)
            return {"error": str(e), "thread_id": thread_id}
