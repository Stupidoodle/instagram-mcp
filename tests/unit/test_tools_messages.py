"""Unit tests for message history tools (thin client → bridge).

The bridge returns messages as JSON dicts (see ``bridge._msg_json``), newest
first. These tests feed those dicts through ``mock_bridge.messages`` and assert
the tools reshape them correctly.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from mcp.server.mcpserver import MCPServer

from instagram_mcp.bridge_client import BridgeClient
from instagram_mcp.tools.messages import register_message_tools


def _msg(
    message_id: str,
    *,
    text: str | None = "hi",
    username: str = "other_user",
    is_from_me: bool = False,
    media_type: str = "text",
    media_url: str | None = None,
    timestamp: str = "2024-01-15T10:30:00",
    seen_since: int | None = None,
    share: dict[str, str] | None = None,
) -> dict[str, Any]:
    """A message in the bridge's JSON shape."""
    return {
        "message_id": message_id,
        "user_id": "111" if is_from_me else "999",
        "username": username,
        "text": text,
        "media_type": media_type,
        "media_url": media_url,
        "timestamp": timestamp,
        "is_from_me": is_from_me,
        "seen_since": seen_since,
        "share": share,
    }


class _Base:
    def setup_method(self) -> None:
        self.mcp = MCPServer("test")
        self.bridge = MagicMock(spec=BridgeClient)
        register_message_tools(self.mcp, self.bridge)

    def _get_tool_fn(self, name: str):
        for tool in self.mcp._tool_manager._tools.values():
            if tool.name == name:
                return tool.fn
        return None


class TestMessageTools(_Base):
    def test_get_messages_success(self) -> None:
        self.bridge.messages.return_value = [
            _msg(
                "111111111",
                text="Hello, this is a test message!",
                username="test_user",
                is_from_me=True,
                seen_since=5,
            ),
        ]

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
        self.bridge.messages.assert_called_once_with("123456789", amount=20)

    def test_get_messages_includes_a_share(self) -> None:
        share = {"kind": "reel", "author": "x", "url": "https://www.instagram.com/reel/A/"}
        self.bridge.messages.return_value = [
            _msg("1", text=None, media_type="reel_share", share=share),
        ]
        tool_fn = self._get_tool_fn("get_messages")
        assert tool_fn is not None
        assert tool_fn(thread_id="1")["messages"][0]["share"] == share

    def test_get_messages_includes_media_url(self) -> None:
        self.bridge.messages.return_value = [
            _msg("1", text=None, media_type="photo", media_url="https://cdn/x.jpg"),
        ]

        result = self._get_tool_fn("get_messages")(thread_id="123456789", amount=20)

        assert result["messages"][0]["media_url"] == "https://cdn/x.jpg"
        assert result["messages"][0]["media_type"] == "photo"

    def test_get_messages_empty(self) -> None:
        self.bridge.messages.return_value = []

        result = self._get_tool_fn("get_messages")(thread_id="123456789", amount=20)

        assert result["count"] == 0
        assert result["messages"] == []

    def test_get_messages_error(self) -> None:
        self.bridge.messages.side_effect = Exception("API Error")

        result = self._get_tool_fn("get_messages")(thread_id="123456789", amount=20)

        assert "error" in result
        assert "API Error" in result["error"]


class TestGetChatLogTool(_Base):
    def test_get_chat_log_basic(self) -> None:
        # Bridge returns newest first.
        self.bridge.messages.return_value = [
            _msg("102", text="not much", username="lena", timestamp="2024-01-15T10:31:00"),
            _msg(
                "101",
                text="hey whats up",
                username="you",
                is_from_me=True,
                timestamp="2024-01-15T10:30:00",
            ),
        ]

        tool_fn = self._get_tool_fn("get_chat_log")
        assert tool_fn is not None
        result = tool_fn(thread_id="123456789", amount=50)

        assert result["thread_id"] == "123456789"
        assert result["count"] == 2
        assert result["offset"] == 0
        lines = result["log"].split("\n")
        assert "YOU: hey whats up" in lines[0]
        assert "lena: not much" in lines[1]

    def test_get_chat_log_skips_action_log(self) -> None:
        self.bridge.messages.return_value = [
            _msg(
                "102",
                text="thread updated",
                media_type="action_log",
                timestamp="2024-01-15T10:31:00",
            ),
            _msg(
                "101", text="hey", username="you", is_from_me=True, timestamp="2024-01-15T10:30:00"
            ),
        ]

        result = self._get_tool_fn("get_chat_log")(thread_id="123456789", amount=50)

        assert result["count"] == 1
        assert "action_log" not in result["log"]

    def test_get_chat_log_media_types(self) -> None:
        self.bridge.messages.return_value = [
            _msg(
                "101",
                text=None,
                username="lena",
                media_type="photo",
                timestamp="2024-01-15T10:30:00",
            ),
        ]

        result = self._get_tool_fn("get_chat_log")(thread_id="123456789", amount=50)

        assert "[photo]" in result["log"]

    def test_get_chat_log_seen_since(self) -> None:
        self.bridge.messages.return_value = [
            _msg(
                "101",
                text="good night",
                username="you",
                is_from_me=True,
                timestamp="2024-01-15T23:00:00",
                seen_since=120,
            ),
        ]

        result = self._get_tool_fn("get_chat_log")(thread_id="123456789", amount=50)

        assert "(seen 2h ago)" in result["log"]

    def test_get_chat_log_with_offset(self) -> None:
        self.bridge.messages.return_value = [
            _msg("103", text="msg3", timestamp="2024-01-15T10:32:00"),
            _msg("102", text="msg2", timestamp="2024-01-15T10:31:00"),
            _msg("101", text="msg1", timestamp="2024-01-15T10:30:00"),
        ]

        # Skip 1 most recent, get next 2.
        result = self._get_tool_fn("get_chat_log")(thread_id="123456789", amount=2, offset=1)

        assert result["count"] == 2
        assert result["offset"] == 1
        assert "msg1" in result["log"]
        assert "msg2" in result["log"]
        assert "msg3" not in result["log"]

    def test_get_chat_log_error(self) -> None:
        self.bridge.messages.side_effect = Exception("API Error")

        result = self._get_tool_fn("get_chat_log")(thread_id="123456789", amount=50)

        assert "error" in result


class TestGetMessagesOffset(_Base):
    def test_offset_pagination(self) -> None:
        self.bridge.messages.return_value = [
            _msg(str(i), text=f"msg{i}", timestamp=f"2024-01-15T10:0{i}:00") for i in range(5)
        ]

        result = self._get_tool_fn("get_messages")(thread_id="123456789", amount=2, offset=2)

        assert result["count"] == 2
        assert result["offset"] == 2
        assert result["messages"][0]["text"] == "msg2"
        assert result["messages"][1]["text"] == "msg3"
        # Bridge is asked for offset + amount messages.
        self.bridge.messages.assert_called_once_with("123456789", amount=4)

    def test_has_more_true(self) -> None:
        self.bridge.messages.return_value = [
            _msg(str(i), text=f"msg{i}", timestamp=f"2024-01-15T10:0{i}:00") for i in range(3)
        ]

        result = self._get_tool_fn("get_messages")(thread_id="123456789", amount=3)

        assert result["has_more"] is True

    def test_has_more_false(self) -> None:
        self.bridge.messages.return_value = [_msg("1", text="only one")]

        result = self._get_tool_fn("get_messages")(thread_id="123456789", amount=20)

        assert result["has_more"] is False

    def test_seen_since_only_on_viewer_messages(self) -> None:
        self.bridge.messages.return_value = [
            _msg(
                "2", text="hey", username="them", is_from_me=False, timestamp="2024-01-15T10:01:00"
            ),
            _msg(
                "1",
                text="hi",
                username="you",
                is_from_me=True,
                timestamp="2024-01-15T10:00:00",
                seen_since=5,
            ),
        ]

        result = self._get_tool_fn("get_messages")(thread_id="123456789", amount=20)

        # Their message (index 0) has no seen_since; viewer's (index 1) does.
        assert "seen_since" not in result["messages"][0]
        assert "seen_since" in result["messages"][1]
        assert result["messages"][1]["seen_since"] == 5
