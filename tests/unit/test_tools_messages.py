"""Unit tests for message operation tools."""

from datetime import datetime
from unittest.mock import MagicMock, patch

from mcp.server.mcpserver import MCPServer

from instagram_mcp.client import InstagramClient
from instagram_mcp.models.schemas import (
    DirectMessage,
    MediaType,
    MessageContent,
    ThreadUser,
)
from instagram_mcp.tools.messages import register_message_tools


class TestMessageTools:
    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.mcp = MCPServer("test")
        self.mock_client = MagicMock(spec=InstagramClient)
        register_message_tools(self.mcp, self.mock_client)

    def _get_tool_fn(self, name: str):
        """Get tool function by name."""
        for tool in self.mcp._tool_manager._tools.values():
            if tool.name == name:
                return tool.fn
        return None

    def test_get_messages_success(self, sample_message: DirectMessage) -> None:
        self.mock_client.get_messages.return_value = [sample_message]

        tool_fn = self._get_tool_fn("get_messages")
        assert tool_fn is not None
        result = tool_fn(thread_id="123456789", amount=20)

        assert result["thread_id"] == "123456789"
        assert result["count"] == 1
        assert result["messages"][0]["sender"] == "test_user"
        assert result["messages"][0]["text"] == "Hello, this is a test message!"
        assert result["messages"][0]["timestamp"] == "2024-01-15T10:30:00"
        assert result["offset"] == 0
        assert "message_id" not in result["messages"][0]
        assert "sender_id" not in result["messages"][0]
        # seen_since included for viewer's own messages
        assert "seen_since" in result["messages"][0]

    def test_get_messages_empty(self) -> None:
        self.mock_client.get_messages.return_value = []

        tool_fn = self._get_tool_fn("get_messages")
        result = tool_fn(thread_id="123456789", amount=20)

        assert result["count"] == 0
        assert result["messages"] == []

    def test_get_messages_error(self) -> None:
        self.mock_client.get_messages.side_effect = Exception("API Error")

        tool_fn = self._get_tool_fn("get_messages")
        result = tool_fn(thread_id="123456789", amount=20)

        assert "error" in result
        assert "API Error" in result["error"]

class TestGetChatLogTool:
    """Tests for the get_chat_log tool."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.mcp = MCPServer("test")
        self.mock_client = MagicMock(spec=InstagramClient)
        register_message_tools(self.mcp, self.mock_client)

    def _get_tool_fn(self, name: str):
        """Get tool function by name."""
        for tool in self.mcp._tool_manager._tools.values():
            if tool.name == name:
                return tool.fn
        return None

    def _create_message(
        self,
        message_id: str,
        text: str | None,
        is_sent_by_viewer: bool = False,
        username: str = "other_user",
        timestamp: datetime | None = None,
        media_type: MediaType = MediaType.TEXT,
        seen_since: int | None = None,
    ) -> DirectMessage:
        """Create a mock DirectMessage for testing."""
        return DirectMessage(
            message_id=message_id,
            thread_id="123456789",
            sender=ThreadUser(user_id="999", username=username),
            content=MessageContent(text=text, media_type=media_type),
            timestamp=timestamp or datetime(2024, 1, 15, 10, 30, 0),
            is_sent_by_viewer=is_sent_by_viewer,
            seen_since=seen_since,
        )

    def test_get_chat_log_basic(self) -> None:
        """Test basic chat log output."""
        messages = [
            self._create_message(
                "102",
                "not much",
                is_sent_by_viewer=False,
                username="lena",
                timestamp=datetime(2024, 1, 15, 10, 31, 0),
            ),
            self._create_message(
                "101",
                "hey whats up",
                is_sent_by_viewer=True,
                username="you",
                timestamp=datetime(2024, 1, 15, 10, 30, 0),
            ),
        ]
        self.mock_client.get_messages.return_value = messages

        tool_fn = self._get_tool_fn("get_chat_log")
        assert tool_fn is not None
        result = tool_fn(thread_id="123456789", amount=50)

        assert result["thread_id"] == "123456789"
        assert result["count"] == 2
        assert result["offset"] == 0
        # Chronological order (oldest first)
        lines = result["log"].split("\n")
        assert "YOU: hey whats up" in lines[0]
        assert "lena: not much" in lines[1]

    def test_get_chat_log_skips_action_log(self) -> None:
        """Test that action_log messages are filtered out."""
        messages = [
            self._create_message(
                "102",
                "thread updated",
                media_type=MediaType.ACTION_LOG,
                timestamp=datetime(2024, 1, 15, 10, 31, 0),
            ),
            self._create_message(
                "101",
                "hey",
                is_sent_by_viewer=True,
                username="you",
                timestamp=datetime(2024, 1, 15, 10, 30, 0),
            ),
        ]
        self.mock_client.get_messages.return_value = messages

        tool_fn = self._get_tool_fn("get_chat_log")
        result = tool_fn(thread_id="123456789", amount=50)

        assert result["count"] == 1
        assert "action_log" not in result["log"]

    def test_get_chat_log_media_types(self) -> None:
        """Test that non-text media shows as [type] markers."""
        messages = [
            self._create_message(
                "101",
                None,
                is_sent_by_viewer=False,
                username="lena",
                media_type=MediaType.PHOTO,
                timestamp=datetime(2024, 1, 15, 10, 30, 0),
            ),
        ]
        self.mock_client.get_messages.return_value = messages

        tool_fn = self._get_tool_fn("get_chat_log")
        result = tool_fn(thread_id="123456789", amount=50)

        assert "[photo]" in result["log"]

    def test_get_chat_log_seen_since(self) -> None:
        """Test seen_since annotation on last viewer message."""
        messages = [
            self._create_message(
                "101",
                "good night",
                is_sent_by_viewer=True,
                username="you",
                timestamp=datetime(2024, 1, 15, 23, 0, 0),
                seen_since=120,
            ),
        ]
        self.mock_client.get_messages.return_value = messages

        tool_fn = self._get_tool_fn("get_chat_log")
        result = tool_fn(thread_id="123456789", amount=50)

        assert "(seen 2h ago)" in result["log"]

    def test_get_chat_log_with_offset(self) -> None:
        """Test offset pagination."""
        messages = [
            self._create_message(
                "103",
                "msg3",
                timestamp=datetime(2024, 1, 15, 10, 32, 0),
            ),
            self._create_message(
                "102",
                "msg2",
                timestamp=datetime(2024, 1, 15, 10, 31, 0),
            ),
            self._create_message(
                "101",
                "msg1",
                timestamp=datetime(2024, 1, 15, 10, 30, 0),
            ),
        ]
        self.mock_client.get_messages.return_value = messages

        tool_fn = self._get_tool_fn("get_chat_log")
        # Skip 1 most recent, get next 2
        result = tool_fn(thread_id="123456789", amount=2, offset=1)

        assert result["count"] == 2
        assert result["offset"] == 1
        assert "msg1" in result["log"]
        assert "msg2" in result["log"]
        assert "msg3" not in result["log"]

    def test_get_chat_log_error(self) -> None:
        """Test error handling."""
        self.mock_client.get_messages.side_effect = Exception("API Error")

        tool_fn = self._get_tool_fn("get_chat_log")
        result = tool_fn(thread_id="123456789", amount=50)

        assert "error" in result


class TestGetMessagesOffset:
    """Tests for get_messages offset pagination."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.mcp = MCPServer("test")
        self.mock_client = MagicMock(spec=InstagramClient)
        register_message_tools(self.mcp, self.mock_client)

    def _get_tool_fn(self, name: str):
        """Get tool function by name."""
        for tool in self.mcp._tool_manager._tools.values():
            if tool.name == name:
                return tool.fn
        return None

    def test_offset_pagination(self) -> None:
        """Test that offset skips the N most recent messages."""
        messages = [
            DirectMessage(
                message_id=str(i),
                thread_id="123456789",
                sender=ThreadUser(user_id="999", username="user"),
                content=MessageContent(text=f"msg{i}", media_type=MediaType.TEXT),
                timestamp=datetime(2024, 1, 15, 10, i, 0),
                is_sent_by_viewer=False,
            )
            for i in range(5)
        ]
        self.mock_client.get_messages.return_value = messages

        tool_fn = self._get_tool_fn("get_messages")
        result = tool_fn(thread_id="123456789", amount=2, offset=2)

        assert result["count"] == 2
        assert result["offset"] == 2
        assert result["messages"][0]["text"] == "msg2"
        assert result["messages"][1]["text"] == "msg3"
        # Client should be called with offset + amount
        self.mock_client.get_messages.assert_called_once_with(thread_id="123456789", amount=4)

    def test_has_more_true(self) -> None:
        """Test has_more is true when more messages exist."""
        messages = [
            DirectMessage(
                message_id=str(i),
                thread_id="123456789",
                sender=ThreadUser(user_id="999", username="user"),
                content=MessageContent(text=f"msg{i}", media_type=MediaType.TEXT),
                timestamp=datetime(2024, 1, 15, 10, i, 0),
                is_sent_by_viewer=False,
            )
            for i in range(3)
        ]
        self.mock_client.get_messages.return_value = messages

        tool_fn = self._get_tool_fn("get_messages")
        result = tool_fn(thread_id="123456789", amount=3)

        assert result["has_more"] is True

    def test_has_more_false(self) -> None:
        """Test has_more is false when fewer messages than requested."""
        messages = [
            DirectMessage(
                message_id="1",
                thread_id="123456789",
                sender=ThreadUser(user_id="999", username="user"),
                content=MessageContent(text="only one", media_type=MediaType.TEXT),
                timestamp=datetime(2024, 1, 15, 10, 0, 0),
                is_sent_by_viewer=False,
            )
        ]
        self.mock_client.get_messages.return_value = messages

        tool_fn = self._get_tool_fn("get_messages")
        result = tool_fn(thread_id="123456789", amount=20)

        assert result["has_more"] is False

    def test_seen_since_only_on_viewer_messages(self) -> None:
        """Test seen_since is included only for viewer's own messages."""
        viewer_msg = DirectMessage(
            message_id="1",
            thread_id="123456789",
            sender=ThreadUser(user_id="111", username="you"),
            content=MessageContent(text="hi", media_type=MediaType.TEXT),
            timestamp=datetime(2024, 1, 15, 10, 0, 0),
            is_sent_by_viewer=True,
            seen_since=5,
        )
        their_msg = DirectMessage(
            message_id="2",
            thread_id="123456789",
            sender=ThreadUser(user_id="222", username="them"),
            content=MessageContent(text="hey", media_type=MediaType.TEXT),
            timestamp=datetime(2024, 1, 15, 10, 1, 0),
            is_sent_by_viewer=False,
            seen_since=None,
        )
        self.mock_client.get_messages.return_value = [their_msg, viewer_msg]

        tool_fn = self._get_tool_fn("get_messages")
        result = tool_fn(thread_id="123456789", amount=20)

        # Viewer message should have seen_since
        viewer_result = result["messages"][1]
        assert "seen_since" in viewer_result
        assert viewer_result["seen_since"] == 5

        # Their message should NOT have seen_since
        their_result = result["messages"][0]
        assert "seen_since" not in their_result


