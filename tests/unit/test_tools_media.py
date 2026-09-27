"""Unit tests for sharing tools (thin client → bridge)."""

from __future__ import annotations

from unittest.mock import MagicMock

from mcp.server.mcpserver import MCPServer

from instagram_mcp.bridge_client import BridgeClient
from instagram_mcp.tools.media import register_media_tools


class TestMediaTools:
    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.mcp = MCPServer("test")
        self.bridge = MagicMock(spec=BridgeClient)
        register_media_tools(self.mcp, self.bridge)

    def _get_tool_fn(self, name: str):
        """Get tool function by name."""
        for tool in self.mcp._tool_manager._tools.values():
            if tool.name == name:
                return tool.fn
        return None

    def test_share_media_success(self) -> None:
        self.bridge.share_media.return_value = {"success": True}

        tool_fn = self._get_tool_fn("share_media")
        assert tool_fn is not None
        result = tool_fn(media_id="333333333", thread_id="123456789")

        assert result["success"] is True
        assert result["media_id"] == "333333333"
        self.bridge.share_media.assert_called_once_with("333333333", "123456789")

    def test_share_media_failure(self) -> None:
        self.bridge.share_media.return_value = {"success": False}

        result = self._get_tool_fn("share_media")(media_id="333333333", thread_id="123456789")

        assert result["success"] is False

    def test_share_media_error(self) -> None:
        self.bridge.share_media.side_effect = Exception("API Error")

        result = self._get_tool_fn("share_media")(media_id="333333333", thread_id="123456789")

        assert "error" in result
        assert "API Error" in result["error"]
        assert result["media_id"] == "333333333"

    def test_share_profile_success(self) -> None:
        self.bridge.share_profile.return_value = {"success": True}

        tool_fn = self._get_tool_fn("share_profile")
        assert tool_fn is not None
        result = tool_fn(user_id="444444444", thread_id="123456789")

        assert result["success"] is True
        assert result["user_id"] == "444444444"
        self.bridge.share_profile.assert_called_once_with("444444444", "123456789")

    def test_share_profile_failure(self) -> None:
        self.bridge.share_profile.return_value = {"success": False}

        result = self._get_tool_fn("share_profile")(user_id="444444444", thread_id="123456789")

        assert result["success"] is False

    def test_share_profile_error(self) -> None:
        self.bridge.share_profile.side_effect = Exception("API Error")

        result = self._get_tool_fn("share_profile")(user_id="444444444", thread_id="123456789")

        assert "error" in result
        assert "API Error" in result["error"]
        assert result["user_id"] == "444444444"
