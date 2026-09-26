"""Unit tests for the channel tools."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from mcp.server.mcpserver import MCPServer

from instagram_mcp.channel import Channel
from instagram_mcp.tools.channel import register_channel_tools, register_messaging_tools

T1 = "340282366841710300949128531777654287254"


class TestSubscriptionTools:
    def setup_method(self) -> None:
        self.mcp = MCPServer("test")
        self.channel = Channel(self_user_id="1", describe_thread=lambda _t: ("Ly", {}))
        register_channel_tools(self.mcp, self.channel)

    def _tool(self, name: str) -> Any:
        return self.mcp._tool_manager._tools[name].fn

    def test_subscribe_list_unsubscribe(self) -> None:
        assert self._tool("subscribe")(chat_id=T1, alias="ly")["subscribed"] == "ly"
        assert self._tool("list_subscriptions")()["subscriptions"] == [f"ly → …{T1[-4:]}"]
        assert self._tool("unsubscribe")(to="ly") == {"success": True, "unsubscribed": "ly"}

    def test_refusals(self) -> None:
        self._tool("subscribe")(chat_id=T1, alias="ly")
        assert "refused" in self._tool("subscribe")(chat_id="1234567", alias="ly")["error"]
        assert "refused" in self._tool("unsubscribe")(to="nope")["error"]
        assert "refused" in self._tool("set_idle")(minutes=-1)["error"]
        assert "refused" in self._tool("set_idle")(minutes=10, to="nope")["error"]

    def test_set_idle(self) -> None:
        self._tool("subscribe")(chat_id=T1, alias="ly")
        assert (
            self._tool("set_idle")(minutes=60)["idle"]
            == "60m for ly (Ly) (resets to 5m when they write)"
        )
        assert self._tool("set_idle")(minutes=0, to="ly")["idle"].startswith("paused")


class TestMessagingTools:
    def setup_method(self) -> None:
        self.mcp = MCPServer("test")
        self.channel = Channel(self_user_id="1", describe_thread=lambda _t: ("Ly", {}))
        self.channel.subscribe(T1, "ly")
        self.client = MagicMock()
        self.client.reply_to_thread.return_value = MagicMock(message_id="m1")
        self.mqtt = MagicMock()
        register_messaging_tools(
            self.mcp, self.client, self.channel, lambda: self.mqtt, Path("/tmp/m")
        )

    def _tool(self, name: str) -> Any:
        return self.mcp._tool_manager._tools[name].fn

    def test_reply_expects_its_echo(self) -> None:
        result = self._tool("reply")(text="hey")
        assert result == {"success": True, "sent": "ly (Ly)", "message_id": "m1"}
        self.client.reply_to_thread.assert_called_once_with(thread_id=T1, text="hey")
        assert self.channel._consume_expected(T1, "text", "hey")

    def test_reply_failures(self) -> None:
        assert "refused" in self._tool("reply")(text="x", to="nope")["error"]
        self.client.reply_to_thread.return_value = None
        assert self._tool("reply")(text="x")["success"] is False
        self.client.reply_to_thread.side_effect = RuntimeError("429")
        assert self._tool("reply")(text="x")["error"] == "429"

    def test_send_file(self, tmp_path: Path) -> None:
        photo = tmp_path / "a.jpg"
        photo.write_bytes(b"x")
        video = tmp_path / "b.mp4"
        video.write_bytes(b"x")
        doc = tmp_path / "c.pdf"
        doc.write_bytes(b"x")
        self.client.send_photo.return_value = MagicMock(message_id="p")
        self.client.send_video.return_value = None
        assert self._tool("send_file")(file_path=str(photo))["message_id"] == "p"
        assert self._tool("send_file")(file_path=str(video))["success"] is False
        assert "only take photos and videos" in self._tool("send_file")(file_path=str(doc))["error"]
        assert "no such file" in self._tool("send_file")(file_path=str(tmp_path / "x.jpg"))["error"]
        assert "refused" in self._tool("send_file")(file_path=str(photo), to="nope")["error"]
        self.client.send_photo.side_effect = RuntimeError("upload")
        assert self._tool("send_file")(file_path=str(photo))["error"] == "upload"

    def test_send_audio(self, tmp_path: Path) -> None:
        clip = tmp_path / "v.ogg"
        clip.write_bytes(b"x")
        self.client.send_voice.return_value = MagicMock(message_id="v")
        assert self._tool("send_audio")(file_path=str(clip))["message_id"] == "v"
        self.client.send_voice.assert_called_once_with(clip, T1)
        assert (
            "no such file" in self._tool("send_audio")(file_path=str(tmp_path / "n.ogg"))["error"]
        )
        assert "refused" in self._tool("send_audio")(file_path=str(clip), to="nope")["error"]
        self.client.send_voice.return_value = None
        assert self._tool("send_audio")(file_path=str(clip))["success"] is False
        self.client.send_voice.side_effect = RuntimeError("ffmpeg")
        assert self._tool("send_audio")(file_path=str(clip))["error"] == "ffmpeg"

    def test_send_typing(self) -> None:
        assert self._tool("send_typing")(composing=False) == {"success": True}
        self.mqtt.indicate_activity.assert_called_once_with(T1, active=False)
        assert "refused" in self._tool("send_typing")(to="nope")["error"]
        self.mqtt.indicate_activity.side_effect = RuntimeError("MQTT not connected")
        assert self._tool("send_typing")()["error"] == "MQTT not connected"
        self.mqtt = None
        assert "no realtime" in self._tool("send_typing")()["error"]

    def test_mark_read_uses_newest(self) -> None:
        self.client.mark_seen.return_value = True
        assert self._tool("mark_read")(message_ids=["5", "30", "12"]) == {"success": True}
        self.client.mark_seen.assert_called_once_with(T1, "30")
        assert self._tool("mark_read")(message_ids=[])["success"] is False
        assert "refused" in self._tool("mark_read")(message_ids=["1"], to="nope")["error"]
        self.client.mark_seen.side_effect = RuntimeError("no")
        assert self._tool("mark_read")(message_ids=["1"])["error"] == "no"

    def test_download_attachment(self, tmp_path: Path) -> None:
        self.client.download_message_media.return_value = tmp_path / "f.jpg"
        assert self._tool("download_attachment")(message_id="m")["path"] == str(tmp_path / "f.jpg")
        self.client.download_message_media.assert_called_once_with(T1, "m", Path("/tmp/m"))
        assert "refused" in self._tool("download_attachment")(message_id="m", to="nope")["error"]
        self.client.download_message_media.side_effect = RuntimeError("view-once")
        assert self._tool("download_attachment")(message_id="m")["error"] == "view-once"

    def test_get_message_ids(self) -> None:
        def message(mid: str, mine: bool, text: str | None) -> MagicMock:
            m = MagicMock(
                message_id=mid, is_sent_by_viewer=mine, timestamp=datetime(2026, 9, 27, 14, 5)
            )
            m.content.text = text
            m.content.media_type.value = "photo"
            return m

        self.client.get_messages.return_value = [
            message("3", True, "see you"),
            message("2", False, "ok"),
            message("1", True, None),
        ]
        assert self._tool("get_message_ids")(limit=5)["messages"] == [
            "3 | 14:05 | see you",
            "1 | 14:05 | [photo]",
        ]
        assert self._tool("get_message_ids")(filter="see")["messages"] == ["3 | 14:05 | see you"]
        assert "refused" in self._tool("get_message_ids")(to="nope")["error"]
        self.client.get_messages.side_effect = RuntimeError("429")
        assert self._tool("get_message_ids")()["error"] == "429"

    def test_unsend(self) -> None:
        self.client.delete_message.return_value = True
        assert self._tool("unsend")(message_id="m1") == {"success": True, "unsent": "ly (Ly)"}
        assert self.channel._consume_expected(T1, "unsend", "m1")
        assert "refused" in self._tool("unsend")(message_id="m1", to="nope")["error"]
        self.client.delete_message.side_effect = RuntimeError("gone")
        assert self._tool("unsend")(message_id="m1")["error"] == "gone"

    def test_react_and_remove(self) -> None:
        self.client.react.return_value = True
        assert self._tool("react")(message_id="m1", emoji="🔥") == {"success": True}
        self.client.react.assert_called_with(T1, "m1", "🔥", remove=False)
        assert self._tool("react")(message_id="m1", emoji="") == {"success": True}
        self.client.react.assert_called_with(T1, "m1", "🔥", remove=True)
        assert self.channel.own_reaction(T1, "m1") is None
        assert "refused" in self._tool("react")(message_id="m1", emoji="x", to="nope")["error"]
        self.client.react.side_effect = RuntimeError("rate")
        assert self._tool("react")(message_id="m1", emoji="x")["error"] == "rate"
