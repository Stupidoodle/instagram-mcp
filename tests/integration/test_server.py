"""Integration tests for MCP server functionality."""

from unittest.mock import MagicMock, patch

from mcp.server.mcpserver import MCPServer

from instagram_mcp.config import Settings
from instagram_mcp.server import create_server
from instagram_mcp.tools import (
    register_media_tools,
    register_message_tools,
    register_thread_tools,
)


class TestServerIntegration:
    def test_server_registers_all_tools(self, mock_settings: Settings) -> None:
        """Test that all tools are properly registered with the server."""
        with (
            patch("instagram_mcp.server.get_settings", return_value=mock_settings),
            patch("instagram_mcp.server.setup_logging"),
            patch("instagram_mcp.server.InstagramClient") as mock_client_class,
        ):
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client

            mcp = create_server(mock_settings)

            # Check that all expected tools are registered
            tool_names = [tool.name for tool in mcp._tool_manager._tools.values()]

            # Thread tools
            assert "list_threads" in tool_names
            assert "get_thread" in tool_names
            assert "search_threads" in tool_names
            assert "get_pending_threads" in tool_names
            assert "hide_thread" in tool_names
            assert "mark_thread_unread" in tool_names
            assert "mute_thread" in tool_names
            assert "unmute_thread" in tool_names

            # Message tools
            assert "send_message" in tool_names
            assert "reply_to_thread" in tool_names
            assert "get_messages" in tool_names
            assert "delete_message" in tool_names

            # Media tools
            assert "send_photo" in tool_names
            assert "send_video" in tool_names
            assert "share_media" in tool_names
            assert "share_profile" in tool_names

    def test_server_metadata(self, mock_settings: Settings) -> None:
        """Test that server metadata is correctly set."""
        with (
            patch("instagram_mcp.server.get_settings", return_value=mock_settings),
            patch("instagram_mcp.server.setup_logging"),
            patch("instagram_mcp.server.InstagramClient") as mock_client_class,
        ):
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client

            mcp = create_server(mock_settings)

            assert mcp.name == "instagram-mcp"


class TestToolRegistration:
    def test_register_thread_tools(self) -> None:
        """Test thread tools registration."""
        mcp = MCPServer("test")
        mock_client = MagicMock()

        register_thread_tools(mcp, mock_client)

        tool_names = [tool.name for tool in mcp._tool_manager._tools.values()]
        assert "list_threads" in tool_names
        assert "get_thread" in tool_names
        assert "search_threads" in tool_names
        assert "get_pending_threads" in tool_names
        assert "hide_thread" in tool_names
        assert "mark_thread_unread" in tool_names
        assert "mute_thread" in tool_names
        assert "unmute_thread" in tool_names

    def test_register_message_tools(self) -> None:
        """Test message tools registration."""
        mcp = MCPServer("test")
        mock_client = MagicMock()

        register_message_tools(mcp, mock_client)

        tool_names = [tool.name for tool in mcp._tool_manager._tools.values()]
        assert "send_message" in tool_names
        assert "reply_to_thread" in tool_names
        assert "get_messages" in tool_names
        assert "delete_message" in tool_names

    def test_register_media_tools(self) -> None:
        """Test media tools registration."""
        mcp = MCPServer("test")
        mock_client = MagicMock()

        register_media_tools(mcp, mock_client)

        tool_names = [tool.name for tool in mcp._tool_manager._tools.values()]
        assert "send_photo" in tool_names
        assert "send_video" in tool_names
        assert "share_media" in tool_names
        assert "share_profile" in tool_names


class TestToolDocstrings:
    def test_all_tools_have_docstrings(self, mock_settings: Settings) -> None:
        """Test that all tools have proper docstrings (used as MCP descriptions)."""
        with (
            patch("instagram_mcp.server.get_settings", return_value=mock_settings),
            patch("instagram_mcp.server.setup_logging"),
            patch("instagram_mcp.server.InstagramClient") as mock_client_class,
        ):
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client

            mcp = create_server(mock_settings)

            for tool in mcp._tool_manager._tools.values():
                assert tool.description is not None, f"Tool {tool.name} has no description"
                assert len(tool.description) > 10, f"Tool {tool.name} has too short description"
