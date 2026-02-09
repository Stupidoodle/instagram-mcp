"""Unit tests for message operation tools."""

from datetime import datetime
from unittest.mock import MagicMock, patch

from mcp.server.fastmcp import FastMCP

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
        self.mcp = FastMCP("test")
        self.mock_client = MagicMock(spec=InstagramClient)
        register_message_tools(self.mcp, self.mock_client)

    def _get_tool_fn(self, name: str):
        """Get tool function by name."""
        for tool in self.mcp._tool_manager._tools.values():
            if tool.name == name:
                return tool.fn
        return None

    def test_send_message_to_users(self, sample_message: DirectMessage) -> None:
        self.mock_client.send_message.return_value = sample_message

        tool_fn = self._get_tool_fn("send_message")
        assert tool_fn is not None
        result = tool_fn(text="Hello", user_ids=["123"], thread_ids=None)

        assert result["success"] is True
        assert result["message_id"] == "111111111"
        assert result["text"] == "Hello"
        self.mock_client.send_message.assert_called_once_with(
            text="Hello", user_ids=["123"], thread_ids=None
        )

    def test_send_message_to_threads(self, sample_message: DirectMessage) -> None:
        self.mock_client.send_message.return_value = sample_message

        tool_fn = self._get_tool_fn("send_message")
        result = tool_fn(text="Hello", user_ids=None, thread_ids=["123456789"])

        assert result["success"] is True

    def test_send_message_no_target(self) -> None:
        tool_fn = self._get_tool_fn("send_message")
        result = tool_fn(text="Hello", user_ids=None, thread_ids=None)

        assert "error" in result
        assert "Must specify either user_ids or thread_ids" in result["error"]

    def test_send_message_failure(self) -> None:
        self.mock_client.send_message.return_value = None

        tool_fn = self._get_tool_fn("send_message")
        result = tool_fn(text="Hello", user_ids=["123"], thread_ids=None)

        assert result["success"] is False
        assert "error" in result

    def test_send_message_error(self) -> None:
        self.mock_client.send_message.side_effect = Exception("API Error")

        tool_fn = self._get_tool_fn("send_message")
        result = tool_fn(text="Hello", user_ids=["123"], thread_ids=None)

        assert "error" in result
        assert "API Error" in result["error"]

    def test_reply_to_thread_success(self, sample_message: DirectMessage) -> None:
        self.mock_client.reply_to_thread.return_value = sample_message

        tool_fn = self._get_tool_fn("reply_to_thread")
        assert tool_fn is not None
        result = tool_fn(thread_id="123456789", text="Reply")

        assert result["success"] is True
        assert result["message_id"] == "111111111"
        self.mock_client.reply_to_thread.assert_called_once_with(
            thread_id="123456789", text="Reply"
        )

    def test_reply_to_thread_failure(self) -> None:
        self.mock_client.reply_to_thread.return_value = None

        tool_fn = self._get_tool_fn("reply_to_thread")
        result = tool_fn(thread_id="123456789", text="Reply")

        assert result["success"] is False
        assert "error" in result

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

    def test_delete_message_success(self) -> None:
        self.mock_client.delete_message.return_value = True

        tool_fn = self._get_tool_fn("delete_message")
        assert tool_fn is not None
        result = tool_fn(thread_id="123456789", message_id="111111111")

        assert result["success"] is True
        assert result["thread_id"] == "123456789"
        assert result["message_id"] == "111111111"
        self.mock_client.delete_message.assert_called_once_with(
            thread_id="123456789", message_id="111111111"
        )

    def test_delete_message_failure(self) -> None:
        self.mock_client.delete_message.return_value = False

        tool_fn = self._get_tool_fn("delete_message")
        result = tool_fn(thread_id="123456789", message_id="111111111")

        assert result["success"] is False

    def test_delete_message_error(self) -> None:
        self.mock_client.delete_message.side_effect = Exception("API Error")

        tool_fn = self._get_tool_fn("delete_message")
        result = tool_fn(thread_id="123456789", message_id="111111111")

        assert "error" in result
        assert "API Error" in result["error"]


class TestWaitForReplyTool:
    """Tests for the wait_for_reply tool."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.mcp = FastMCP("test")
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
        text: str,
        is_sent_by_viewer: bool = False,
        username: str = "other_user",
        timestamp: datetime | None = None,
    ) -> DirectMessage:
        """Create a mock DirectMessage for testing."""
        return DirectMessage(
            message_id=message_id,
            thread_id="123456789",
            sender=ThreadUser(user_id="999", username=username),
            content=MessageContent(text=text, media_type=MediaType.TEXT),
            timestamp=timestamp or datetime(2024, 1, 15, 10, 30, 0),
            is_sent_by_viewer=is_sent_by_viewer,
        )

    @patch("instagram_mcp.tools.messages.time.sleep")
    @patch("instagram_mcp.tools.messages.time.time")
    def test_wait_for_reply_success(self, mock_time: MagicMock, mock_sleep: MagicMock) -> None:
        """Test successful detection of new reply."""
        # Use a counter for time.time() - sync phase + main loop needs many calls
        time_values = iter([0, 0, 0, 3, 6, 6, 17, 17])
        mock_time.side_effect = lambda: next(time_values)

        # Our baseline message (timestamp T0)
        baseline_msg = self._create_message(
            "100", "Hello", is_sent_by_viewer=True, timestamp=datetime(2024, 1, 15, 10, 0, 0)
        )
        # Their new message (timestamp > T0)
        new_msg = self._create_message(
            "101", "Hey there!", is_sent_by_viewer=False, timestamp=datetime(2024, 1, 15, 10, 1, 0)
        )

        self.mock_client.get_messages.side_effect = [
            [baseline_msg],  # Sync phase - finds our baseline
            [baseline_msg],  # First poll - no new messages
            [new_msg, baseline_msg],  # Second poll - new message!
            [new_msg, baseline_msg],  # Grace period poll
        ]

        tool_fn = self._get_tool_fn("wait_for_reply")
        assert tool_fn is not None

        result = tool_fn(
            thread_id="123456789",
            timeout_minutes=5,
            poll_interval_seconds=3,
            double_text_grace_period_seconds=10,
        )

        assert result["success"] is True
        assert len(result["new_messages"]) == 1
        assert result["new_messages"][0]["text"] == "Hey there!"
        assert result["new_messages"][0]["sender"] == "other_user"

    @patch("instagram_mcp.tools.messages.time.sleep")
    @patch("instagram_mcp.tools.messages.time.time")
    def test_wait_for_reply_double_text(self, mock_time: MagicMock, mock_sleep: MagicMock) -> None:
        """Test catching double texts within grace period."""
        time_values = iter([0, 0, 0, 3, 3, 8, 8, 15, 15])
        mock_time.side_effect = lambda: next(time_values)

        baseline_msg = self._create_message(
            "100", "Hello", is_sent_by_viewer=True, timestamp=datetime(2024, 1, 15, 10, 0, 0)
        )
        msg1 = self._create_message(
            "101", "Hey!", is_sent_by_viewer=False, timestamp=datetime(2024, 1, 15, 10, 1, 0)
        )
        msg2 = self._create_message(
            "102", "Also...", is_sent_by_viewer=False, timestamp=datetime(2024, 1, 15, 10, 2, 0)
        )

        self.mock_client.get_messages.side_effect = [
            [baseline_msg],  # Sync phase
            [msg1, baseline_msg],  # First message detected
            [msg2, msg1, baseline_msg],  # Second message during grace period
            [msg2, msg1, baseline_msg],  # Grace period done
        ]

        tool_fn = self._get_tool_fn("wait_for_reply")
        result = tool_fn(
            thread_id="123456789",
            timeout_minutes=5,
            poll_interval_seconds=3,
            double_text_grace_period_seconds=10,
        )

        assert result["success"] is True
        assert len(result["new_messages"]) == 2
        # Should be in chronological order (oldest first)
        assert result["new_messages"][0]["text"] == "Hey!"
        assert result["new_messages"][1]["text"] == "Also..."

    @patch("instagram_mcp.tools.messages.time.sleep")
    @patch("instagram_mcp.tools.messages.time.time")
    def test_wait_for_reply_short_timeout(
        self, mock_time: MagicMock, mock_sleep: MagicMock
    ) -> None:
        """Test short timeout behavior (5 minutes)."""
        # Sync phase uses time.time() for timeout check, main loop for elapsed
        time_values = iter([0, 0, 0, 150, 301])
        mock_time.side_effect = lambda: next(time_values)

        baseline_msg = self._create_message(
            "100", "Hello", is_sent_by_viewer=True, timestamp=datetime(2024, 1, 15, 10, 0, 0)
        )
        self.mock_client.get_messages.return_value = [baseline_msg]

        tool_fn = self._get_tool_fn("wait_for_reply")
        result = tool_fn(
            thread_id="123456789",
            timeout_minutes=5,
            poll_interval_seconds=3,
            double_text_grace_period_seconds=10,
        )

        assert result["timeout"] is True
        assert result["waited_minutes"] == 5

    @patch("instagram_mcp.tools.messages.time.sleep")
    @patch("instagram_mcp.tools.messages.time.time")
    def test_wait_for_reply_long_timeout(self, mock_time: MagicMock, mock_sleep: MagicMock) -> None:
        """Test long timeout behavior (e.g., 120 minutes cooldown)."""
        time_values = iter([0, 0, 0, 3600, 7201])
        mock_time.side_effect = lambda: next(time_values)

        baseline_msg = self._create_message(
            "100", "Hello", is_sent_by_viewer=True, timestamp=datetime(2024, 1, 15, 10, 0, 0)
        )
        self.mock_client.get_messages.return_value = [baseline_msg]

        tool_fn = self._get_tool_fn("wait_for_reply")
        result = tool_fn(
            thread_id="123456789",
            timeout_minutes=120,  # 2 hour cooldown
            poll_interval_seconds=3,
            double_text_grace_period_seconds=10,
        )

        assert result["timeout"] is True
        assert "waited_minutes" in result
        assert result["waited_minutes"] == 120

    @patch("instagram_mcp.tools.messages.time.sleep")
    @patch("instagram_mcp.tools.messages.time.time")
    def test_wait_for_reply_ignores_own_messages(
        self, mock_time: MagicMock, mock_sleep: MagicMock
    ) -> None:
        """Test that our own messages are ignored."""
        time_values = iter([0, 0, 0, 3, 6, 6, 17, 17])
        mock_time.side_effect = lambda: next(time_values)

        baseline_msg = self._create_message(
            "100", "Hello", is_sent_by_viewer=True, timestamp=datetime(2024, 1, 15, 10, 0, 0)
        )
        # Our follow-up has timestamp > baseline, but should be ignored (is_sent_by_viewer)
        our_msg = self._create_message(
            "101",
            "Anyone there?",
            is_sent_by_viewer=True,
            timestamp=datetime(2024, 1, 15, 10, 1, 0),
        )
        # Their reply has timestamp > baseline, should be detected
        their_msg = self._create_message(
            "102", "Yeah!", is_sent_by_viewer=False, timestamp=datetime(2024, 1, 15, 10, 2, 0)
        )

        self.mock_client.get_messages.side_effect = [
            [baseline_msg],  # Sync phase
            [our_msg, baseline_msg],  # Our follow-up - should be ignored
            [their_msg, our_msg, baseline_msg],  # Their reply
            [their_msg, our_msg, baseline_msg],
        ]

        tool_fn = self._get_tool_fn("wait_for_reply")
        result = tool_fn(
            thread_id="123456789",
            timeout_minutes=5,
            poll_interval_seconds=3,
            double_text_grace_period_seconds=10,
        )

        assert result["success"] is True
        assert len(result["new_messages"]) == 1
        assert result["new_messages"][0]["text"] == "Yeah!"

    def test_wait_for_reply_error(self) -> None:
        """Test error handling."""
        self.mock_client.get_messages.side_effect = Exception("Connection failed")

        tool_fn = self._get_tool_fn("wait_for_reply")
        result = tool_fn(thread_id="123456789", timeout_minutes=5)

        assert "error" in result
        assert "Connection failed" in result["error"]

    @patch("instagram_mcp.tools.messages.time.sleep")
    @patch("instagram_mcp.tools.messages.time.time")
    def test_wait_for_reply_ignores_action_logs(
        self, mock_time: MagicMock, mock_sleep: MagicMock
    ) -> None:
        """Test that action_log messages (likes/reactions) are ignored.

        Bug: When someone likes a message, wait_for_reply was returning
        immediately with the like as a "new message". It should ignore
        action_logs and keep waiting for actual text replies.

        Scenario:
        1. We send "schreib mir wenn es dir besser geht"
        2. They like the message (action_log)
        3. wait_for_reply should NOT return - should keep waiting
        4. Only timeout after the specified duration
        """
        # Provide many time values to handle all the time.time() calls
        # The bug causes it to enter grace period and call time.time() repeatedly
        # After fix: should just poll until timeout without grace period nonsense
        time_values = [0] * 5 + [3, 6, 9, 12, 61]  # Eventually timeout
        time_iter = iter(time_values)
        mock_time.side_effect = lambda: next(time_iter, 61)

        # Our baseline message
        baseline_msg = DirectMessage(
            message_id="100",
            thread_id="123456789",
            sender=ThreadUser(user_id="999", username="you"),
            content=MessageContent(
                text="schreib mir wenn es dir besser geht",
                media_type=MediaType.TEXT,
            ),
            timestamp=datetime(2024, 1, 15, 10, 23, 39),
            is_sent_by_viewer=True,
        )

        # Their like (action_log) - should be IGNORED
        like_action = DirectMessage(
            message_id="101",
            thread_id="123456789",
            sender=ThreadUser(user_id="888", username="a.nymee"),
            content=MessageContent(
                text="Liked a message",
                media_type=MediaType.ACTION_LOG,
            ),
            timestamp=datetime(2024, 1, 15, 10, 23, 50),  # After baseline
            is_sent_by_viewer=False,
        )

        # Both polls return the same state: baseline + like
        self.mock_client.get_messages.return_value = [like_action, baseline_msg]

        tool_fn = self._get_tool_fn("wait_for_reply")
        result = tool_fn(
            thread_id="123456789",
            timeout_minutes=1,  # 1 minute timeout
            poll_interval_seconds=3,
            double_text_grace_period_seconds=10,
        )

        # Should timeout, NOT return with success=True and the like as a message
        assert result.get("timeout") is True, (
            f"Expected timeout, but got success with action_log: {result}"
        )
        assert result.get("success") is not True, (
            "action_log should not trigger successful reply detection"
        )

    @patch("instagram_mcp.tools.messages.time.sleep")
    @patch("instagram_mcp.tools.messages.time.time")
    def test_wait_for_reply_race_condition(
        self, mock_time: MagicMock, mock_sleep: MagicMock
    ) -> None:
        """Test race condition: message arrives while we're responding.

        Scenario:
        1. They send msg 100 at T0
        2. We respond with msg 101 at T1
        3. They send msg 102 at T2 WHILE we're responding
        4. We call wait_for_reply - should detect msg 102!

        With timestamp-based detection, any message from them with
        timestamp > our baseline (T1) should be detected.
        """
        time_values = iter([0, 0, 0, 3, 3, 15, 15])
        mock_time.side_effect = lambda: next(time_values)

        # Their first message at T0
        their_first_msg = self._create_message(
            "100", "Hey!", is_sent_by_viewer=False, timestamp=datetime(2024, 1, 15, 10, 0, 0)
        )
        # Our response at T1 (this becomes our baseline)
        our_response = self._create_message(
            "101", "Hi there!", is_sent_by_viewer=True, timestamp=datetime(2024, 1, 15, 10, 1, 0)
        )
        # Their message at T2 > T1 (should be detected!)
        their_race_msg = self._create_message(
            "102",
            "Also wanted to ask...",
            is_sent_by_viewer=False,
            timestamp=datetime(2024, 1, 15, 10, 2, 0),
        )

        # When wait_for_reply is called, the thread already has all 3 messages
        self.mock_client.get_messages.return_value = [
            their_race_msg,
            our_response,
            their_first_msg,
        ]

        tool_fn = self._get_tool_fn("wait_for_reply")
        result = tool_fn(
            thread_id="123456789",
            timeout_minutes=5,
            poll_interval_seconds=3,
            double_text_grace_period_seconds=10,
        )

        # Should detect msg 102 because its timestamp (T2) > our baseline (T1)
        assert result.get("success") is True, f"Expected success, got: {result}"
        assert len(result["new_messages"]) == 1
        assert result["new_messages"][0]["text"] == "Also wanted to ask..."


class TestSendAndCheckTool:
    """Tests for the send_and_check tool."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.mcp = FastMCP("test")
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
        text: str,
        is_sent_by_viewer: bool = False,
        username: str = "other_user",
        timestamp: datetime | None = None,
    ) -> DirectMessage:
        """Create a mock DirectMessage for testing."""
        return DirectMessage(
            message_id=message_id,
            thread_id="123456789",
            sender=ThreadUser(user_id="999", username=username),
            content=MessageContent(text=text, media_type=MediaType.TEXT),
            timestamp=timestamp or datetime(2024, 1, 15, 10, 30, 0),
            is_sent_by_viewer=is_sent_by_viewer,
        )

    @patch("instagram_mcp.tools.messages.time.sleep")
    def test_send_and_check_no_interjection(self, mock_sleep: MagicMock) -> None:
        """Test send_and_check when no interjection occurs."""
        # Setup: their last message before we send
        their_old_msg = self._create_message(
            "100", "hey", is_sent_by_viewer=False, timestamp=datetime(2024, 1, 15, 10, 0, 0)
        )
        # Our sent message
        our_sent_msg = self._create_message(
            "101", "what's up", is_sent_by_viewer=True, timestamp=datetime(2024, 1, 15, 10, 1, 0)
        )

        # Mock reply_to_thread to return our message
        self.mock_client.reply_to_thread.return_value = our_sent_msg

        # Mock get_messages: first call (baseline), then synced state
        self.mock_client.get_messages.side_effect = [
            [their_old_msg],  # Pre-send baseline
            [our_sent_msg, their_old_msg],  # Sync check - our msg visible
            [our_sent_msg, their_old_msg],  # Post-send check - no new msgs
        ]

        tool_fn = self._get_tool_fn("send_and_check")
        assert tool_fn is not None

        result = tool_fn(thread_id="123456789", text="what's up")

        assert result["success"] is True
        assert result["has_interjection"] is False
        assert "interjection" not in result

    @patch("instagram_mcp.tools.messages.time.sleep")
    def test_send_and_check_with_interjection(self, mock_sleep: MagicMock) -> None:
        """Test send_and_check detects interjection from them."""
        # Their old message (baseline)
        their_old_msg = self._create_message(
            "100", "hey", is_sent_by_viewer=False, timestamp=datetime(2024, 1, 15, 10, 0, 0)
        )
        # Our sent message
        our_sent_msg = self._create_message(
            "101", "what's up", is_sent_by_viewer=True, timestamp=datetime(2024, 1, 15, 10, 1, 0)
        )
        # Their interjection (sent AFTER our message)
        their_interjection = self._create_message(
            "102",
            "wait hold on",
            is_sent_by_viewer=False,
            timestamp=datetime(2024, 1, 15, 10, 1, 5),
        )

        self.mock_client.reply_to_thread.return_value = our_sent_msg

        self.mock_client.get_messages.side_effect = [
            [their_old_msg],  # Pre-send baseline
            [
                their_interjection,
                our_sent_msg,
                their_old_msg,
            ],  # Sync - our msg + their interjection
            [their_interjection, our_sent_msg, their_old_msg],  # Post-send check
        ]

        tool_fn = self._get_tool_fn("send_and_check")
        result = tool_fn(thread_id="123456789", text="what's up")

        assert result["success"] is True
        assert result["has_interjection"] is True
        assert result["interjection"]["text"] == "wait hold on"
        assert result["interjection"]["sender"] == "other_user"

    @patch("instagram_mcp.tools.messages.time.sleep")
    def test_send_and_check_sync_timeout(self, mock_sleep: MagicMock) -> None:
        """Test send_and_check handles sync timeout gracefully."""
        their_old_msg = self._create_message(
            "100", "hey", is_sent_by_viewer=False, timestamp=datetime(2024, 1, 15, 10, 0, 0)
        )
        our_sent_msg = self._create_message(
            "101", "what's up", is_sent_by_viewer=True, timestamp=datetime(2024, 1, 15, 10, 1, 0)
        )

        self.mock_client.reply_to_thread.return_value = our_sent_msg

        # Message never appears in sync (simulating API delay)
        self.mock_client.get_messages.return_value = [their_old_msg]

        tool_fn = self._get_tool_fn("send_and_check")
        result = tool_fn(
            thread_id="123456789",
            text="what's up",
            sync_timeout_seconds=1,  # Short timeout for test
        )

        # Should still succeed even if sync timed out
        assert result["success"] is True

    def test_send_and_check_send_failure(self) -> None:
        """Test send_and_check handles send failure."""
        self.mock_client.get_messages.return_value = []
        self.mock_client.reply_to_thread.return_value = None  # Send failed

        tool_fn = self._get_tool_fn("send_and_check")
        result = tool_fn(thread_id="123456789", text="hello")

        assert result["success"] is False
        assert "error" in result

    def test_send_and_check_exception(self) -> None:
        """Test send_and_check handles exceptions."""
        self.mock_client.get_messages.side_effect = Exception("API Error")

        tool_fn = self._get_tool_fn("send_and_check")
        result = tool_fn(thread_id="123456789", text="hello")

        assert "error" in result
        assert "API Error" in result["error"]


class TestGetChatLogTool:
    """Tests for the get_chat_log tool."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.mcp = FastMCP("test")
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
        self.mcp = FastMCP("test")
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


class TestWaitForReplyMQTT:
    """Tests for the MQTT fast path of wait_for_reply."""

    def _make_mqtt(self, connected: bool = True) -> MagicMock:
        """Create a mock MQTTManager."""
        from instagram_mcp.mqtt.router import EventRouter

        mqtt = MagicMock()
        mqtt.is_connected = connected
        mqtt.router = EventRouter()
        return mqtt

    def test_message_event_returned(self) -> None:
        """MQTT path returns MessageEvent as new_messages."""
        import threading

        from instagram_mcp.mqtt.events import MessageEvent
        from instagram_mcp.tools.messages import _wait_for_reply_mqtt

        mqtt = self._make_mqtt()
        event = MessageEvent(
            thread_id="T1", item_id="I1", user_id=42, text="yo", item_type="text", timestamp=1000
        )

        def deliver() -> None:
            import time

            time.sleep(0.05)
            mqtt.router.deliver(event)

        t = threading.Thread(target=deliver)
        t.start()
        result = _wait_for_reply_mqtt(
            mqtt, "T1", timeout_minutes=1, double_text_grace_period_seconds=1
        )
        t.join()

        assert result["success"] is True
        assert len(result["new_messages"]) == 1
        assert result["new_messages"][0]["sender"] == "42"
        assert result["new_messages"][0]["text"] == "yo"

    def test_timeout_returns_timeout(self) -> None:
        """MQTT path returns timeout when no events arrive."""
        from instagram_mcp.tools.messages import _wait_for_reply_mqtt

        mqtt = self._make_mqtt()
        # Use a very short timeout to avoid slow test
        result = _wait_for_reply_mqtt(
            mqtt, "T1", timeout_minutes=0, double_text_grace_period_seconds=1
        )

        assert result["timeout"] is True

    def test_seen_event_enriches_result(self) -> None:
        """SeenEvent during wait is included as metadata."""
        import threading

        from instagram_mcp.mqtt.events import MessageEvent, SeenEvent
        from instagram_mcp.tools.messages import _wait_for_reply_mqtt

        mqtt = self._make_mqtt()
        seen = SeenEvent(thread_id="T1", user_id=99, item_id="I1", timestamp=2_000_000)
        msg = MessageEvent(
            thread_id="T1", item_id="I2", user_id=99, text="hi", item_type="text", timestamp=3000
        )

        def deliver() -> None:
            import time

            time.sleep(0.03)
            mqtt.router.deliver(seen)
            time.sleep(0.03)
            mqtt.router.deliver(msg)

        t = threading.Thread(target=deliver)
        t.start()
        result = _wait_for_reply_mqtt(
            mqtt, "T1", timeout_minutes=1, double_text_grace_period_seconds=1
        )
        t.join()

        assert result["success"] is True
        assert result.get("seen") is True

    def test_typing_event_enriches_result(self) -> None:
        """TypingEvent during wait sets typing flag."""
        import threading

        from instagram_mcp.mqtt.events import MessageEvent, TypingEvent
        from instagram_mcp.tools.messages import _wait_for_reply_mqtt

        mqtt = self._make_mqtt()
        typing = TypingEvent(thread_id="T1", user_id=99, activity_status=1, ttl=5000)
        msg = MessageEvent(
            thread_id="T1", item_id="I1", user_id=99, text="hey", item_type="text", timestamp=1000
        )

        def deliver() -> None:
            import time

            time.sleep(0.03)
            mqtt.router.deliver(typing)
            time.sleep(0.03)
            mqtt.router.deliver(msg)

        t = threading.Thread(target=deliver)
        t.start()
        result = _wait_for_reply_mqtt(
            mqtt, "T1", timeout_minutes=1, double_text_grace_period_seconds=1
        )
        t.join()

        assert result["success"] is True
        assert result.get("typing") is True

    def test_reaction_event_enriches_result(self) -> None:
        """ReactionEvent during wait is included as metadata."""
        import threading

        from instagram_mcp.mqtt.events import MessageEvent, ReactionEvent
        from instagram_mcp.tools.messages import _wait_for_reply_mqtt

        mqtt = self._make_mqtt()
        reaction = ReactionEvent(
            thread_id="T1", item_id="I0", user_id=99, reaction_type="like", emoji="\u2764\ufe0f"
        )
        msg = MessageEvent(
            thread_id="T1", item_id="I1", user_id=99, text="nice", item_type="text", timestamp=1000
        )

        def deliver() -> None:
            import time

            time.sleep(0.03)
            mqtt.router.deliver(reaction)
            time.sleep(0.03)
            mqtt.router.deliver(msg)

        t = threading.Thread(target=deliver)
        t.start()
        result = _wait_for_reply_mqtt(
            mqtt, "T1", timeout_minutes=1, double_text_grace_period_seconds=1
        )
        t.join()

        assert result["success"] is True
        assert "reactions" in result
        assert result["reactions"][0]["emoji"] == "\u2764\ufe0f"

    def test_unsend_event_enriches_result(self) -> None:
        """UnsendEvent during wait is included as unsent item_id list."""
        import threading

        from instagram_mcp.mqtt.events import MessageEvent, UnsendEvent
        from instagram_mcp.tools.messages import _wait_for_reply_mqtt

        mqtt = self._make_mqtt()
        unsend = UnsendEvent(thread_id="T1", item_id="I0", user_id=99)
        msg = MessageEvent(
            thread_id="T1", item_id="I1", user_id=99, text="nvm", item_type="text", timestamp=1000
        )

        def deliver() -> None:
            import time

            time.sleep(0.03)
            mqtt.router.deliver(unsend)
            time.sleep(0.03)
            mqtt.router.deliver(msg)

        t = threading.Thread(target=deliver)
        t.start()
        result = _wait_for_reply_mqtt(
            mqtt, "T1", timeout_minutes=1, double_text_grace_period_seconds=1
        )
        t.join()

        assert result["success"] is True
        assert "unsent" in result
        assert result["unsent"][0] == "I0"

    def test_seen_on_timeout_with_disconnect(self) -> None:
        """Seen events are returned even when no messages arrive (timeout via disconnect)."""
        import threading

        from instagram_mcp.mqtt.events import SeenEvent
        from instagram_mcp.tools.messages import _wait_for_reply_mqtt

        mqtt = self._make_mqtt()
        seen = SeenEvent(thread_id="T1", user_id=99, item_id="I1", timestamp=1_000_000)

        def deliver_and_disconnect() -> None:
            import time

            time.sleep(0.03)
            mqtt.router.deliver(seen)
            time.sleep(0.05)
            mqtt.is_connected = False  # Triggers early exit

        t = threading.Thread(target=deliver_and_disconnect)
        t.start()
        result = _wait_for_reply_mqtt(
            mqtt, "T1", timeout_minutes=1, double_text_grace_period_seconds=1
        )
        t.join()

        assert result["timeout"] is True
        assert result.get("seen") is True

    def test_disconnected_returns_early(self) -> None:
        """MQTT path returns early when connection is lost."""
        from instagram_mcp.tools.messages import _wait_for_reply_mqtt

        mqtt = self._make_mqtt(connected=False)
        result = _wait_for_reply_mqtt(
            mqtt, "T1", timeout_minutes=1, double_text_grace_period_seconds=1
        )

        assert result["timeout"] is True


class TestSendAndCheckMQTT:
    """Tests for the MQTT fast path of send_and_check."""

    def _make_mqtt(self, connected: bool = True) -> MagicMock:
        """Create a mock MQTTManager."""
        from instagram_mcp.mqtt.router import EventRouter

        mqtt = MagicMock()
        mqtt.is_connected = connected
        mqtt.router = EventRouter()
        return mqtt

    def test_no_interjection(self) -> None:
        """MQTT path returns no interjection when no events arrive."""
        from instagram_mcp.tools.messages import _send_and_check_mqtt

        mqtt = self._make_mqtt()
        q = mqtt.router.subscribe("T1")
        result = _send_and_check_mqtt(mqtt, q, "T1", "M1")

        assert result["success"] is True
        assert result["has_interjection"] is False
        assert "interjection" not in result

    def test_interjection_detected(self) -> None:
        """MQTT path detects a message interjection."""
        import threading

        from instagram_mcp.mqtt.events import MessageEvent
        from instagram_mcp.tools.messages import _send_and_check_mqtt

        mqtt = self._make_mqtt()
        q = mqtt.router.subscribe("T1")
        interjection = MessageEvent(
            thread_id="T1", item_id="I1", user_id=42, text="wait", item_type="text", timestamp=1000
        )

        def deliver() -> None:
            import time

            time.sleep(0.03)
            mqtt.router.deliver(interjection)

        t = threading.Thread(target=deliver)
        t.start()
        result = _send_and_check_mqtt(mqtt, q, "T1", "M1")
        t.join()

        assert result["has_interjection"] is True
        assert result["interjection"]["text"] == "wait"
        assert result["interjection"]["sender"] == "42"

    def test_interjection_with_username(self) -> None:
        """MQTT path resolves user_id to username via user_map."""
        import threading

        from instagram_mcp.mqtt.events import MessageEvent
        from instagram_mcp.tools.messages import _send_and_check_mqtt

        mqtt = self._make_mqtt()
        q = mqtt.router.subscribe("T1")
        interjection = MessageEvent(
            thread_id="T1", item_id="I1", user_id=42, text="wait", item_type="text", timestamp=1000
        )

        def deliver() -> None:
            import time

            time.sleep(0.03)
            mqtt.router.deliver(interjection)

        t = threading.Thread(target=deliver)
        t.start()
        result = _send_and_check_mqtt(
            mqtt, q, "T1", "M1", user_map={"42": "shaina"}
        )
        t.join()

        assert result["interjection"]["sender"] == "shaina"

    def test_self_echo_filtered(self) -> None:
        """MQTT path ignores self-echoes (own message pushed back by MQTT)."""
        import threading

        from instagram_mcp.mqtt.events import MessageEvent
        from instagram_mcp.tools.messages import _send_and_check_mqtt

        mqtt = self._make_mqtt()
        q = mqtt.router.subscribe("T1")
        self_echo = MessageEvent(
            thread_id="T1", item_id="I1", user_id=999, text="my msg", item_type="text", timestamp=1000
        )

        def deliver() -> None:
            import time

            time.sleep(0.03)
            mqtt.router.deliver(self_echo)

        t = threading.Thread(target=deliver)
        t.start()
        result = _send_and_check_mqtt(
            mqtt, q, "T1", "M1", self_user_id="999"
        )
        t.join()

        assert result["has_interjection"] is False

    def test_seen_event_enriches(self) -> None:
        """SeenEvent during sync window is included as metadata."""
        import threading

        from instagram_mcp.mqtt.events import SeenEvent
        from instagram_mcp.tools.messages import _send_and_check_mqtt

        mqtt = self._make_mqtt()
        q = mqtt.router.subscribe("T1")
        seen = SeenEvent(thread_id="T1", user_id=99, item_id="I1", timestamp=2_000_000)

        def deliver() -> None:
            import time

            time.sleep(0.03)
            mqtt.router.deliver(seen)

        t = threading.Thread(target=deliver)
        t.start()
        result = _send_and_check_mqtt(mqtt, q, "T1", "M1")
        t.join()

        assert result["has_interjection"] is False
        assert result.get("seen") is True

    def test_typing_event_enriches(self) -> None:
        """TypingEvent during sync window sets typing flag."""
        import threading

        from instagram_mcp.mqtt.events import TypingEvent
        from instagram_mcp.tools.messages import _send_and_check_mqtt

        mqtt = self._make_mqtt()
        q = mqtt.router.subscribe("T1")
        typing = TypingEvent(thread_id="T1", user_id=99, activity_status=1, ttl=5000)

        def deliver() -> None:
            import time

            time.sleep(0.03)
            mqtt.router.deliver(typing)

        t = threading.Thread(target=deliver)
        t.start()
        result = _send_and_check_mqtt(mqtt, q, "T1", "M1")
        t.join()

        assert result.get("typing") is True

    def test_reaction_event_enriches(self) -> None:
        """ReactionEvent during sync window — not captured in trimmed MQTT path."""
        from instagram_mcp.tools.messages import _send_and_check_mqtt

        mqtt = self._make_mqtt()
        q = mqtt.router.subscribe("T1")
        # Reactions are no longer tracked in the trimmed MQTT send_and_check
        result = _send_and_check_mqtt(mqtt, q, "T1", "M1")

        assert result["success"] is True
        assert result["has_interjection"] is False
