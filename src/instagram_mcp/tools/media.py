"""Media messaging tools for Instagram MCP Server.

This module provides MCP tools for sending photos, videos, and sharing
media content via Instagram Direct Messages.
"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from instagram_mcp.client import InstagramClient

logger = logging.getLogger("instagram_mcp")


def register_media_tools(mcp: "MCPServer", client: "InstagramClient") -> None:
    """Register media messaging tools with the MCP server.

    Args:
        mcp: MCP server instance.
        client: Instagram client instance.
    """

    @mcp.tool()
    def send_photo(
        path: str,
        user_ids: list[str] | None = None,
        thread_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Send a photo to users or existing threads.

        Args:
            path: Path to the photo file to send.
            user_ids: List of user IDs to send to (creates new threads if needed).
            thread_ids: List of thread IDs to send to (existing conversations).

        Returns:
            Object with 'success', 'message_id', 'thread_id', and 'path' on success,
            or 'error' on failure. Must provide either user_ids or thread_ids.
            Supported formats: jpg, jpeg, png, gif, webp.
        """
        if not user_ids and not thread_ids:
            return {"error": "Must specify either user_ids or thread_ids"}

        photo_path = Path(path)
        if not photo_path.exists():
            return {"error": f"Photo file not found: {path}"}

        if photo_path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
            return {"error": f"Unsupported image format: {photo_path.suffix}"}

        try:
            message = client.send_photo(
                path=photo_path,
                user_ids=user_ids,
                thread_ids=thread_ids,
            )
            if message:
                return {
                    "success": True,
                    "message_id": message.message_id,
                    "thread_id": message.thread_id,
                    "path": str(photo_path),
                }
            return {"success": False, "error": "Failed to send photo"}
        except Exception as e:
            logger.exception("Error sending photo")
            return {"error": str(e)}

    @mcp.tool()
    def send_video(
        path: str,
        user_ids: list[str] | None = None,
        thread_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Send a video to users or existing threads.

        Args:
            path: Path to the video file to send.
            user_ids: List of user IDs to send to (creates new threads if needed).
            thread_ids: List of thread IDs to send to (existing conversations).

        Returns:
            Object with 'success', 'message_id', 'thread_id', and 'path' on success,
            or 'error' on failure. Must provide either user_ids or thread_ids.
            Supported formats: mp4, mov, avi, mkv.
        """
        if not user_ids and not thread_ids:
            return {"error": "Must specify either user_ids or thread_ids"}

        video_path = Path(path)
        if not video_path.exists():
            return {"error": f"Video file not found: {path}"}

        if video_path.suffix.lower() not in {".mp4", ".mov", ".avi", ".mkv"}:
            return {"error": f"Unsupported video format: {video_path.suffix}"}

        try:
            message = client.send_video(
                path=video_path,
                user_ids=user_ids,
                thread_ids=thread_ids,
            )
            if message:
                return {
                    "success": True,
                    "message_id": message.message_id,
                    "thread_id": message.thread_id,
                    "path": str(video_path),
                }
            return {"success": False, "error": "Failed to send video"}
        except Exception as e:
            logger.exception("Error sending video")
            return {"error": str(e)}

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
