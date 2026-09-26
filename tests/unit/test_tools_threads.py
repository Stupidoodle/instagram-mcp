"""Unit tests for thread management tools."""

from unittest.mock import MagicMock

from mcp.server.mcpserver import MCPServer

from instagram_mcp.client import InstagramClient
from instagram_mcp.models.schemas import DirectThread
from instagram_mcp.tools.threads import register_thread_tools


class TestThreadTools:
    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.mcp = MCPServer("test")
        self.mock_client = MagicMock(spec=InstagramClient)
        register_thread_tools(self.mcp, self.mock_client)

    def _get_tool_fn(self, name: str):
        """Get tool function by name."""
        for tool in self.mcp._tool_manager._tools.values():
            if tool.name == name:
                return tool.fn
        return None

    def test_list_threads_success(self, sample_thread: DirectThread) -> None:
        self.mock_client.get_threads.return_value = [sample_thread]

        tool_fn = self._get_tool_fn("list_threads")
        assert tool_fn is not None
        result = tool_fn(amount=20)

        assert result["count"] == 1
        assert result["threads"][0]["thread_id"] == "123456789"
        assert result["threads"][0]["thread_title"] == "Test Conversation"
        self.mock_client.get_threads.assert_called_once_with(amount=20)

    def test_list_threads_empty(self) -> None:
        self.mock_client.get_threads.return_value = []

        tool_fn = self._get_tool_fn("list_threads")
        result = tool_fn(amount=20)

        assert result["count"] == 0
        assert result["threads"] == []

    def test_list_threads_error(self) -> None:
        self.mock_client.get_threads.side_effect = Exception("API Error")

        tool_fn = self._get_tool_fn("list_threads")
        result = tool_fn(amount=20)

        assert "error" in result
        assert "API Error" in result["error"]

    def test_get_thread_success(self, sample_thread: DirectThread) -> None:
        self.mock_client.get_thread.return_value = sample_thread

        tool_fn = self._get_tool_fn("get_thread")
        assert tool_fn is not None
        result = tool_fn(thread_id="123456789", amount=20)

        assert result["thread_id"] == "123456789"
        assert result["thread_title"] == "Test Conversation"
        assert "messages" in result

    def test_get_thread_error(self) -> None:
        self.mock_client.get_thread.side_effect = Exception("Not found")

        tool_fn = self._get_tool_fn("get_thread")
        result = tool_fn(thread_id="123456789", amount=20)

        assert "error" in result
        assert "Not found" in result["error"]

    def test_search_threads_success(self, sample_thread: DirectThread) -> None:
        self.mock_client.search_threads.return_value = [sample_thread]

        tool_fn = self._get_tool_fn("search_threads")
        assert tool_fn is not None
        result = tool_fn(query="test")

        assert result["query"] == "test"
        assert result["count"] == 1
        assert result["threads"][0]["thread_id"] == "123456789"
        self.mock_client.search_threads.assert_called_once_with(query="test")

    def test_search_threads_empty(self) -> None:
        self.mock_client.search_threads.return_value = []

        tool_fn = self._get_tool_fn("search_threads")
        result = tool_fn(query="nonexistent")

        assert result["count"] == 0
        assert result["threads"] == []

    def test_get_pending_threads(self, sample_thread: DirectThread) -> None:
        self.mock_client.get_pending_threads.return_value = [sample_thread]

        tool_fn = self._get_tool_fn("get_pending_threads")
        assert tool_fn is not None
        result = tool_fn()

        assert result["count"] == 1
        assert result["threads"][0]["thread_id"] == "123456789"

    def test_get_pending_threads_empty(self) -> None:
        self.mock_client.get_pending_threads.return_value = []

        tool_fn = self._get_tool_fn("get_pending_threads")
        result = tool_fn()

        assert result["count"] == 0
        assert result["threads"] == []

    def test_hide_thread_success(self) -> None:
        self.mock_client.hide_thread.return_value = True

        tool_fn = self._get_tool_fn("hide_thread")
        assert tool_fn is not None
        result = tool_fn(thread_id="123456789")

        assert result["success"] is True
        assert result["thread_id"] == "123456789"

    def test_hide_thread_failure(self) -> None:
        self.mock_client.hide_thread.return_value = False

        tool_fn = self._get_tool_fn("hide_thread")
        result = tool_fn(thread_id="123456789")

        assert result["success"] is False

    def test_mark_thread_unread_success(self) -> None:
        self.mock_client.mark_thread_unread.return_value = True

        tool_fn = self._get_tool_fn("mark_thread_unread")
        assert tool_fn is not None
        result = tool_fn(thread_id="123456789")

        assert result["success"] is True
        assert result["thread_id"] == "123456789"

    def test_mute_thread_success(self) -> None:
        self.mock_client.mute_thread.return_value = True

        tool_fn = self._get_tool_fn("mute_thread")
        assert tool_fn is not None
        result = tool_fn(thread_id="123456789")

        assert result["success"] is True
        assert result["thread_id"] == "123456789"

    def test_unmute_thread_success(self) -> None:
        self.mock_client.unmute_thread.return_value = True

        tool_fn = self._get_tool_fn("unmute_thread")
        assert tool_fn is not None
        result = tool_fn(thread_id="123456789")

        assert result["success"] is True
        assert result["thread_id"] == "123456789"
