"""Unit tests for the channel tools."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from mcp.server.mcpserver import MCPServer

from instagram_mcp.bridge_client import BridgeClient
from instagram_mcp.channel import Channel
from instagram_mcp.tools.channel import register_channel_tools, register_messaging_tools

T1 = "340282366841700000000000000000000000001"


class TestSubscriptionTools:
    def setup_method(self) -> None:
        self.mcp = MCPServer("test")
        self.channel = Channel(self_user_id="1", describe_thread=lambda _t: ("Alex", {}))
        register_channel_tools(self.mcp, self.channel)

    def _tool(self, name: str) -> Any:
        return self.mcp._tool_manager._tools[name].fn

    def test_subscribe_list_unsubscribe(self) -> None:
        assert self._tool("subscribe")(chat_id=T1, alias="alex")["subscribed"] == "alex"
        assert self._tool("list_subscriptions")()["subscriptions"] == [f"alex → …{T1[-4:]}"]
        assert self._tool("unsubscribe")(to="alex") == {"success": True, "unsubscribed": "alex"}

    def test_refusals(self) -> None:
        self._tool("subscribe")(chat_id=T1, alias="alex")
        assert "refused" in self._tool("subscribe")(chat_id="1234567", alias="alex")["error"]
        assert "refused" in self._tool("unsubscribe")(to="nope")["error"]
        assert "refused" in self._tool("set_idle")(minutes=-1)["error"]
        assert "refused" in self._tool("set_idle")(minutes=10, to="nope")["error"]

    def test_set_idle(self) -> None:
        self._tool("subscribe")(chat_id=T1, alias="alex")
        assert (
            self._tool("set_idle")(minutes=60)["idle"]
            == "60m for alex (Alex) (resets to 5m when they write)"
        )
        assert self._tool("set_idle")(minutes=0, to="alex")["idle"].startswith("paused")


class TestMessagingTools:
    def setup_method(self) -> None:
        self.mcp = MCPServer("test")
        self.channel = Channel(self_user_id="1", describe_thread=lambda _t: ("Alex", {}))
        self.channel.subscribe(T1, "alex")
        self.bridge = MagicMock(spec=BridgeClient)
        register_messaging_tools(self.mcp, self.bridge, self.channel)

    def _tool(self, name: str) -> Any:
        return self.mcp._tool_manager._tools[name].fn

    def test_reply_expects_its_echo(self) -> None:
        self.bridge.send.return_value = {"success": True, "message_id": "m1"}
        result = self._tool("reply")(text="hey")
        assert result == {"success": True, "sent": "alex (Alex)", "message_id": "m1"}
        self.bridge.send.assert_called_once_with(T1, "hey")
        # The echo is registered so the MQTT echo of our own send is dropped.
        assert self.channel._consume_expected(T1, "text", "hey")

    def test_reply_failures(self) -> None:
        assert "refused" in self._tool("reply")(text="x", to="nope")["error"]
        self.bridge.send.return_value = {"success": False}
        assert self._tool("reply")(text="x")["success"] is False
        self.bridge.send.side_effect = RuntimeError("429")
        assert self._tool("reply")(text="x")["error"] == "429"

    def test_send_file(self) -> None:
        # The path is read on the bridge host, so the tool only validates the suffix.
        self.bridge.send_media.return_value = {"success": True, "message_id": "p"}
        assert self._tool("send_file")(file_path="/host/a.jpg")["message_id"] == "p"
        self.bridge.send_media.assert_called_once_with(T1, "/host/a.jpg", "photo")

        self.bridge.send_media.return_value = {"success": False}
        assert self._tool("send_file")(file_path="/host/b.mp4")["success"] is False

        pdf = self._tool("send_file")(file_path="/host/c.pdf")
        assert "only take photos and videos" in pdf["error"]
        assert "refused" in self._tool("send_file")(file_path="/host/a.jpg", to="nope")["error"]

        self.bridge.send_media.side_effect = RuntimeError("upload")
        assert self._tool("send_file")(file_path="/host/a.jpg")["error"] == "upload"

    def test_send_file_kind_video(self) -> None:
        self.bridge.send_media.return_value = {"success": True, "message_id": "v"}
        self._tool("send_file")(file_path="/host/clip.mov")
        self.bridge.send_media.assert_called_once_with(T1, "/host/clip.mov", "video")

    def test_send_audio(self) -> None:
        self.bridge.send_voice.return_value = {"success": True, "message_id": "v"}
        assert self._tool("send_audio")(file_path="/host/v.ogg")["message_id"] == "v"
        self.bridge.send_voice.assert_called_once_with(T1, "/host/v.ogg")
        assert "refused" in self._tool("send_audio")(file_path="/host/v.ogg", to="nope")["error"]
        self.bridge.send_voice.return_value = {"success": False}
        assert self._tool("send_audio")(file_path="/host/v.ogg")["success"] is False
        self.bridge.send_voice.side_effect = RuntimeError("ffmpeg")
        assert self._tool("send_audio")(file_path="/host/v.ogg")["error"] == "ffmpeg"

    def test_send_typing(self) -> None:
        self.bridge.typing.return_value = {"success": True}
        assert self._tool("send_typing")(composing=False) == {"success": True}
        self.bridge.typing.assert_called_once_with(T1, active=False)
        assert "refused" in self._tool("send_typing")(to="nope")["error"]
        self.bridge.typing.side_effect = RuntimeError("bridge down")
        assert self._tool("send_typing")()["error"] == "bridge down"

    def test_mark_read_uses_newest(self) -> None:
        self.bridge.mark_read.return_value = {"success": True}
        assert self._tool("mark_read")(message_ids=["5", "30", "12"]) == {"success": True}
        self.bridge.mark_read.assert_called_once_with(T1, "30")
        assert self._tool("mark_read")(message_ids=[])["success"] is False
        assert "refused" in self._tool("mark_read")(message_ids=["1"], to="nope")["error"]
        self.bridge.mark_read.side_effect = RuntimeError("no")
        assert self._tool("mark_read")(message_ids=["1"])["error"] == "no"

    def test_download_attachment(self) -> None:
        self.bridge.download.return_value = {"success": True, "path": "/host/f.jpg"}
        assert self._tool("download_attachment")(message_id="m")["path"] == "/host/f.jpg"
        self.bridge.download.assert_called_once_with(T1, "m")
        assert "refused" in self._tool("download_attachment")(message_id="m", to="nope")["error"]
        self.bridge.download.side_effect = RuntimeError("view-once")
        assert self._tool("download_attachment")(message_id="m")["error"] == "view-once"

    def test_get_message_ids(self) -> None:
        self.bridge.messages.return_value = [
            {
                "message_id": "3",
                "is_from_me": True,
                "text": "see you",
                "media_type": "text",
                "timestamp": "2026-09-27T14:05:00",
            },
            {
                "message_id": "2",
                "is_from_me": False,
                "text": "ok",
                "media_type": "text",
                "timestamp": "2026-09-27T14:05:00",
            },
            {
                "message_id": "1",
                "is_from_me": True,
                "text": None,
                "media_type": "photo",
                "timestamp": "2026-09-27T14:05:00",
            },
        ]
        assert self._tool("get_message_ids")(limit=5)["messages"] == [
            "3 | 14:05 | see you",
            "1 | 14:05 | [photo]",
        ]
        assert self._tool("get_message_ids")(filter="see")["messages"] == ["3 | 14:05 | see you"]
        assert "refused" in self._tool("get_message_ids")(to="nope")["error"]
        self.bridge.messages.side_effect = RuntimeError("429")
        assert self._tool("get_message_ids")()["error"] == "429"

    def test_unsend(self) -> None:
        self.bridge.unsend.return_value = {"success": True}
        assert self._tool("unsend")(message_id="m1") == {"success": True, "unsent": "alex (Alex)"}
        self.bridge.unsend.assert_called_once_with(T1, "m1")
        assert self.channel._consume_expected(T1, "unsend", "m1")
        assert "refused" in self._tool("unsend")(message_id="m1", to="nope")["error"]
        self.bridge.unsend.side_effect = RuntimeError("gone")
        assert self._tool("unsend")(message_id="m1")["error"] == "gone"

    def test_react_and_remove(self) -> None:
        self.bridge.react.return_value = {"success": True}
        assert self._tool("react")(message_id="m1", emoji="🔥") == {"success": True}
        self.bridge.react.assert_called_with(T1, "m1", "🔥", remove=False)
        # An empty emoji removes the reaction we just remembered.
        assert self._tool("react")(message_id="m1", emoji="") == {"success": True}
        self.bridge.react.assert_called_with(T1, "m1", "🔥", remove=True)
        assert self.channel.own_reaction(T1, "m1") is None
        assert "refused" in self._tool("react")(message_id="m1", emoji="x", to="nope")["error"]
        self.bridge.react.side_effect = RuntimeError("rate")
        assert self._tool("react")(message_id="m1", emoji="x")["error"] == "rate"
