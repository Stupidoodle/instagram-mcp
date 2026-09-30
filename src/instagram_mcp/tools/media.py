"""Sharing tools for Instagram MCP Server.

Share posts and profiles into a DM thread, over the instagram-bridge. Photos,
videos and voice messages go through the channel tools (send_file, send_audio).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from instagram_mcp.bridge_client import BridgeClient

logger = logging.getLogger("instagram_mcp")


def register_media_tools(mcp: MCPServer, bridge: BridgeClient) -> None:
    """Register the sharing tools with the MCP server."""

    @mcp.tool()
    def share_media(media_id: str, thread_id: str) -> dict[str, Any]:
        """Share an Instagram post into a thread.

        Args:
            media_id: ID of the Instagram media/post to share.
            thread_id: Thread to share it into.
        """
        try:
            return {**bridge.share_media(media_id, thread_id), "media_id": media_id}
        except Exception as e:
            logger.exception("Error sharing media")
            return {"error": str(e), "media_id": media_id}

    @mcp.tool()
    def share_profile(user_id: str, thread_id: str) -> dict[str, Any]:
        """Share a user's Instagram profile into a thread.

        Args:
            user_id: ID of the user profile to share.
            thread_id: Thread to share it into.
        """
        try:
            return {**bridge.share_profile(user_id, thread_id), "user_id": user_id}
        except Exception as e:
            logger.exception("Error sharing a profile")
            return {"error": str(e), "user_id": user_id}
