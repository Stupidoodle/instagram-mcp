"""Sharing tools for Instagram MCP Server.

Share posts and profiles into DMs. Photos, videos and voice messages go
through the channel tools (send_file, send_audio).
"""

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from instagram_mcp.client import InstagramClient

logger = logging.getLogger("instagram_mcp")


def register_media_tools(mcp: MCPServer, client: InstagramClient) -> None:
    """Register the sharing tools with the MCP server.

    Args:
        mcp: MCP server instance.
        client: Instagram client instance.
    """

    @mcp.tool()
    def share_media(
        media_id: str,
        user_ids: list[str] | None = None,
        thread_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Share an Instagram post to users or threads.

        Args:
            media_id: ID of the Instagram media/post to share.
            user_ids: List of user IDs to send to.
            thread_ids: List of thread IDs to send to.

        Returns:
            Object with 'success' boolean and 'media_id'.
            Must provide either user_ids or thread_ids.
        """
        if not user_ids and not thread_ids:
            return {"error": "Must specify either user_ids or thread_ids"}

        try:
            success = client.share_media(
                media_id=media_id,
                user_ids=user_ids,
                thread_ids=thread_ids,
            )
            return {"success": success, "media_id": media_id}
        except Exception as e:
            logger.exception("Error sharing media %s", media_id)
            return {"error": str(e), "media_id": media_id}

    @mcp.tool()
    def share_profile(
        user_id: str,
        target_user_ids: list[str] | None = None,
        thread_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Share a user's Instagram profile to users or threads.

        Args:
            user_id: ID of the user profile to share.
            target_user_ids: List of user IDs to send the profile to.
            thread_ids: List of thread IDs to send the profile to.

        Returns:
            Object with 'success' boolean and 'user_id'.
            Must provide either target_user_ids or thread_ids.
        """
        if not target_user_ids and not thread_ids:
            return {"error": "Must specify either target_user_ids or thread_ids"}

        try:
            success = client.share_profile(
                user_id=user_id,
                target_user_ids=target_user_ids,
                thread_ids=thread_ids,
            )
            return {"success": success, "user_id": user_id}
        except Exception as e:
            logger.exception("Error sharing profile %s", user_id)
            return {"error": str(e), "user_id": user_id}
