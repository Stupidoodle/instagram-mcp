"""Unit tests for thread management tools (thin client → bridge)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from mcp.server.mcpserver import MCPServer

from instagram_mcp.bridge_client import BridgeClient
from instagram_mcp.tools.threads import register_thread_tools


def _thread_dict(**overrides: Any) -> dict[str, Any]:
    """A thread in the bridge's JSON shape (what BridgeClient returns)."""
    thread = {
        "thread_id": "123456789",
        "thread_title": "Test Conversation",
        "users": [
            {"user_id": "123456", "username": "test_user", "full_name": "Test User"},
            {"user_id": "789012", "username": "other_user", "full_name": "Other User"},
        ],
        "is_group": False,
        "is_muted": False,
        "unread": True,
        "last_activity_at": "2024-01-15T10:35:00",
    }
    thread.update(overrides)
    return thread


class TestThreadTools:
    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.mcp = MCPServer("test")
        self.bridge = MagicMock(spec=BridgeClient)
        register_thread_tools(self.mcp, self.bridge)

    def _get_tool_fn(self, name: str):
        """Get tool function by name."""
        for tool in self.mcp._tool_manager._tools.values():
            if tool.name == name:
                return tool.fn
        return None

    def test_list_threads_success(self) -> None:
        self.bridge.threads.return_value = [_thread_dict()]

        tool_fn = self._get_tool_fn("list_threads")
        assert tool_fn is not None
        result = tool_fn(amount=20)

        assert result["count"] == 1
        assert result["threads"][0]["thread_id"] == "123456789"
        assert result["threads"][0]["thread_title"] == "Test Conversation"
        self.bridge.threads.assert_called_once_with(amount=20)

    def test_list_threads_title_falls_back_to_usernames(self) -> None:
        self.bridge.threads.return_value = [_thread_dict(thread_title="")]

        result = self._get_tool_fn("list_threads")(amount=20)

        assert result["threads"][0]["thread_title"] == "test_user, other_user"

    def test_list_threads_empty(self) -> None:
        self.bridge.threads.return_value = []

        result = self._get_tool_fn("list_threads")(amount=20)

        assert result["count"] == 0
        assert result["threads"] == []

    def test_list_threads_error(self) -> None:
        self.bridge.threads.side_effect = Exception("API Error")

        result = self._get_tool_fn("list_threads")(amount=20)

        assert "error" in result
        assert "API Error" in result["error"]

    def test_get_thread_success(self) -> None:
        self.bridge.thread.return_value = _thread_dict(messages=[])

        tool_fn = self._get_tool_fn("get_thread")
        assert tool_fn is not None
        result = tool_fn(thread_id="123456789", amount=20)

        assert result["thread_id"] == "123456789"
        assert result["thread_title"] == "Test Conversation"
        assert "messages" in result
        self.bridge.thread.assert_called_once_with("123456789", amount=20)

    def test_get_thread_bridge_error_dict_is_passed_through(self) -> None:
        self.bridge.thread.return_value = {"error": "thread_id required"}

        result = self._get_tool_fn("get_thread")(thread_id="123456789", amount=20)

        assert result == {"error": "thread_id required"}

    def test_get_thread_error(self) -> None:
        self.bridge.thread.side_effect = Exception("Not found")

        result = self._get_tool_fn("get_thread")(thread_id="123456789", amount=20)

        assert "error" in result
        assert "Not found" in result["error"]

    def test_search_threads_success(self) -> None:
        self.bridge.search.return_value = [_thread_dict()]

        tool_fn = self._get_tool_fn("search_threads")
        assert tool_fn is not None
        result = tool_fn(query="test")

        assert result["query"] == "test"
        assert result["count"] == 1
        assert result["threads"][0]["thread_id"] == "123456789"
        self.bridge.search.assert_called_once_with("test")

    def test_search_threads_empty(self) -> None:
        self.bridge.search.return_value = []

        result = self._get_tool_fn("search_threads")(query="nonexistent")

        assert result["count"] == 0
        assert result["threads"] == []

    def test_get_pending_threads(self) -> None:
        self.bridge.pending.return_value = [_thread_dict()]

        tool_fn = self._get_tool_fn("get_pending_threads")
        assert tool_fn is not None
        result = tool_fn()

        assert result["count"] == 1
        assert result["threads"][0]["thread_id"] == "123456789"

    def test_get_pending_threads_empty(self) -> None:
        self.bridge.pending.return_value = []

        result = self._get_tool_fn("get_pending_threads")()

        assert result["count"] == 0
        assert result["threads"] == []

    def test_hide_thread_success(self) -> None:
        self.bridge.hide.return_value = {"success": True}

        tool_fn = self._get_tool_fn("hide_thread")
        assert tool_fn is not None
        result = tool_fn(thread_id="123456789")

        assert result["success"] is True
        assert result["thread_id"] == "123456789"
        self.bridge.hide.assert_called_once_with("123456789")

    def test_hide_thread_failure(self) -> None:
        self.bridge.hide.return_value = {"success": False}

        result = self._get_tool_fn("hide_thread")(thread_id="123456789")

        assert result["success"] is False

    def test_hide_thread_error(self) -> None:
        self.bridge.hide.side_effect = Exception("boom")

        result = self._get_tool_fn("hide_thread")(thread_id="123456789")

        assert "error" in result
        assert result["thread_id"] == "123456789"

    def test_mark_thread_unread_success(self) -> None:
        self.bridge.mark_unread.return_value = {"success": True}

        tool_fn = self._get_tool_fn("mark_thread_unread")
        assert tool_fn is not None
        result = tool_fn(thread_id="123456789")

        assert result["success"] is True
        assert result["thread_id"] == "123456789"

    def test_mute_thread_success(self) -> None:
        self.bridge.mute.return_value = {"success": True}

        tool_fn = self._get_tool_fn("mute_thread")
        assert tool_fn is not None
        result = tool_fn(thread_id="123456789")

        assert result["success"] is True
        assert result["thread_id"] == "123456789"

    def test_unmute_thread_success(self) -> None:
        self.bridge.unmute.return_value = {"success": True}

        tool_fn = self._get_tool_fn("unmute_thread")
        assert tool_fn is not None
        result = tool_fn(thread_id="123456789")

        assert result["success"] is True
        assert result["thread_id"] == "123456789"
