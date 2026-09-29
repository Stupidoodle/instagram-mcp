"""Unit tests for the bridge's event/JSON serialization (no network)."""

from __future__ import annotations

import time
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import uvicorn

from instagram_mcp import bridge
from instagram_mcp.bridge import Gateway, _msg_json, _thread_json, event_stream, event_to_dict
from instagram_mcp.client import InstagramClientError
from instagram_mcp.mqtt.events import (
    Event,
    MessageEvent,
    ReactionEvent,
    SeenEvent,
    ThreadEvent,
    TypingEvent,
    UnsendEvent,
)
from instagram_mcp.shares import Share

if TYPE_CHECKING:
    from pathlib import Path


class TestEventToDict:
    def test_message(self) -> None:
        event = MessageEvent("t1", "i1", 5, "hey", "text", 1_700_000_000_000, edited=True)
        assert event_to_dict(event) == {
            "type": "message",
            "thread_id": "t1",
            "item_id": "i1",
            "user_id": "5",
            "text": "hey",
            "item_type": "text",
            "timestamp": 1_700_000_000_000,
            "edited": True,
            "link_url": None,
            "link_title": None,
            "share": None,
            "media_path": None,
            "transcript": None,
            "media_error": None,
            "view_mode": None,
        }

    def test_message_with_a_share(self) -> None:
        share = Share(kind="reel", url="https://www.instagram.com/reel/A/", author="x")
        event = MessageEvent("t1", "i1", 5, None, "xma_clip", 1, share=share)
        assert event_to_dict(event)["share"] == share.to_dict()

    def test_reaction(self) -> None:
        event = ReactionEvent("t1", "i1", 5, "emojis", "🔥")
        assert event_to_dict(event) == {
            "type": "reaction",
            "thread_id": "t1",
            "item_id": "i1",
            "user_id": "5",
            "reaction_type": "emojis",
            "emoji": "🔥",
        }

    def test_read(self) -> None:
        event = SeenEvent("t1", 5, "i1", 42)
        assert event_to_dict(event) == {
            "type": "read",
            "thread_id": "t1",
            "user_id": "5",
            "item_id": "i1",
            "timestamp": 42,
        }

    def test_typing(self) -> None:
        event = TypingEvent("t1", 5, 1, 10000)
        assert event_to_dict(event) == {
            "type": "typing",
            "thread_id": "t1",
            "user_id": "5",
            "activity_status": 1,
            "ttl": 10000,
        }

    def test_unsent(self) -> None:
        event = UnsendEvent("t1", "i1", 5)
        assert event_to_dict(event) == {
            "type": "unsent",
            "thread_id": "t1",
            "item_id": "i1",
            "user_id": "5",
        }

    def test_thread(self) -> None:
        event = ThreadEvent("t1", "add", "/direct_v2/inbox/threads/t1")
        assert event_to_dict(event) == {
            "type": "thread",
            "thread_id": "t1",
            "op": "add",
            "path": "/direct_v2/inbox/threads/t1",
        }

    def test_unknown_event_is_skipped(self) -> None:
        assert event_to_dict(Event("t1")) is None


class TestJsonSerializers:
    def test_thread_json(self) -> None:
        thread = SimpleNamespace(
            thread_id="123",
            thread_title="Test",
            users=[SimpleNamespace(user_id="1", username="a", full_name="A")],
            is_group=False,
            is_muted=True,
            unread=False,
            last_activity_at=None,
        )
        data = _thread_json(thread)
        assert data["thread_id"] == "123"
        assert data["users"] == [{"user_id": "1", "username": "a", "full_name": "A"}]
        assert data["is_muted"] is True
        assert data["last_activity_at"] is None

    def test_msg_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TZ", "Europe/Zurich")
        time.tzset()
        msg = SimpleNamespace(
            message_id="m1",
            sender=SimpleNamespace(user_id="1", username="a"),
            content=SimpleNamespace(
                text="hi",
                media_type=SimpleNamespace(value="text"),
                media_url=None,
                link_url=None,
                link_title=None,
                share=Share(kind="post", author="x"),
            ),
            timestamp=datetime(2024, 1, 15, 10, 30, 0),
            is_sent_by_viewer=True,
            seen_since=5,
            reactions=[SimpleNamespace(user_id="2", emoji="🩷")],
        )
        data = _msg_json(msg)
        assert data == {
            "message_id": "m1",
            "user_id": "1",
            "username": "a",
            "text": "hi",
            "media_type": "text",
            "media_url": None,
            "link_url": None,
            "link_title": None,
            "share": {"kind": "post", "author": "x"},
            "timestamp": "2024-01-15T10:30:00+01:00",
            "is_from_me": True,
            "seen_since": 5,
            "reactions": [{"user_id": "2", "emoji": "🩷"}],
        }


def _bare_gateway() -> Gateway:
    g = Gateway.__new__(Gateway)  # no login: only the subscriber plumbing is exercised
    g._subscribers = set()
    return g


class TestShutdown:
    async def test_closing_ends_every_stream(self) -> None:
        g = _bare_gateway()
        q = g.add_subscriber()
        g._fan_out("data: {}\n\n")
        g.close_streams()
        lines = [line async for line in event_stream(g, q)]
        assert lines == [": connected\n\n", "data: {}\n\n"]
        assert g._subscribers == set()

    async def test_replayed_frames_come_before_live_ones(self) -> None:
        g = _bare_gateway()
        q = g.add_subscriber()
        g._fan_out("id: b-3\ndata: {}\n\n")
        g.close_streams()
        lines = [line async for line in event_stream(g, q, ["event: hello\n", "id: b-2\n"])]
        assert lines == [": connected\n\n", "event: hello\n", "id: b-2\n", "id: b-3\ndata: {}\n\n"]

    async def test_the_server_closes_streams_before_waiting_on_connections(self) -> None:
        g = MagicMock()
        server = bridge.BridgeServer(uvicorn.Config(MagicMock()))
        with (
            patch.object(bridge, "gateway", g),
            patch.object(uvicorn.Server, "shutdown", AsyncMock()) as base,
        ):
            await server.shutdown()
        g.close_streams.assert_called_once_with()
        base.assert_awaited_once()


class TestDescribeShare:
    def _gateway(self, tmp_path: Path) -> Gateway:
        g = _bare_gateway()
        g.settings = MagicMock(instagram_media_dir=tmp_path)
        g.client = MagicMock()
        return g

    def test_caption_and_cover(self, tmp_path: Path) -> None:
        g = self._gateway(tmp_path)
        share = Share(kind="reel", media_id="1", preview_url="https://cdn/c.jpg")
        g.client.share_details.return_value = replace(share, caption="so good")
        event = MessageEvent("thread42", "i9", 5, None, "xma_clip", 1, share=share)
        with patch("instagram_mcp.bridge.save_url", return_value=tmp_path / "c.jpg") as save:
            out = g._describe_share(event)
        save.assert_called_once_with("https://cdn/c.jpg", tmp_path / "thread42", "read42-i9-cover")
        assert out.share is not None and out.share.caption == "so good"
        assert (out.media_path, out.media_error) == (str(tmp_path / "c.jpg"), None)

    def test_failures_are_reported_not_raised(self, tmp_path: Path) -> None:
        g = self._gateway(tmp_path)
        share = Share(kind="reel", media_id="1", preview_url="https://cdn/c.jpg")
        g.client.share_details.side_effect = RuntimeError("gone")
        event = MessageEvent("t1", "i9", 5, None, "xma_clip", 1, share=share)
        with patch("instagram_mcp.bridge.save_url", side_effect=RuntimeError("404")):
            out = g._describe_share(event)
        assert out.share == share
        assert out.media_path is None
        assert out.media_error == "caption lookup failed: gone; cover download failed: 404"


class TestOpenShare:
    @staticmethod
    def _gateway(tmp_path: Path, files: list[Path]) -> Gateway:
        g = _bare_gateway()
        g.settings = MagicMock(instagram_media_dir=tmp_path)
        g.client = MagicMock()
        g.client.open_share.return_value = (Share(kind="reel", caption="hi"), files)
        g._transcribe = AsyncMock(return_value="hallo")  # type: ignore[method-assign]
        return g

    async def test_photos_and_a_preview_per_video(self, tmp_path: Path) -> None:
        video, photo = tmp_path / "t1" / "v.mp4", tmp_path / "t1" / "p.jpg"
        g = self._gateway(tmp_path, [photo, video])
        with (
            patch("instagram_mcp.bridge.duration", return_value=7.04),
            patch("instagram_mcp.bridge.frame_strip", return_value=tmp_path / "f.jpg"),
        ):
            opened = await g.open_share("t1", "m1")
        g.client.open_share.assert_called_once_with("t1", "m1", tmp_path / "t1")
        g._transcribe.assert_not_called()
        assert opened == {
            "share": {"kind": "reel", "caption": "hi"},
            "photos": [str(photo)],
            "videos": [{"file": str(video), "seconds": 7.0, "preview": str(tmp_path / "f.jpg")}],
        }

    async def test_a_failed_preview_is_listed(self, tmp_path: Path) -> None:
        g = self._gateway(tmp_path, [tmp_path / "v.mp4"])
        with patch("instagram_mcp.bridge.duration", side_effect=RuntimeError("no ffprobe")):
            opened = await g.open_share("t1", "m1")
        assert opened["videos"] == [{"file": str(tmp_path / "v.mp4")}]
        assert opened["errors"] == ["preview of v.mp4: no ffprobe"]


class TestTranscribeShare:
    async def test_reuses_the_download(self, tmp_path: Path) -> None:
        g = TestOpenShare._gateway(tmp_path, [])
        loud, mute = tmp_path / "t1" / "a.mp4", tmp_path / "t1" / "b.mp4"
        with (
            patch("instagram_mcp.bridge.share_downloads", return_value=[loud, mute]),
            patch("instagram_mcp.bridge.has_audio", side_effect=[True, False]),
            patch("instagram_mcp.bridge.to_m4a", return_value=tmp_path / "a.m4a") as audio,
        ):
            result = await g.transcribe_share("t1", "m1")
        g.client.open_share.assert_not_called()
        audio.assert_called_once_with(loud, loud.parent)
        assert result == {
            "transcripts": [
                {"file": str(loud), "text": "hallo"},
                {"file": str(mute), "silent": True},
            ]
        }

    async def test_downloads_first_when_needed(self, tmp_path: Path) -> None:
        g = TestOpenShare._gateway(tmp_path, [tmp_path / "v.mp4"])
        with (
            patch("instagram_mcp.bridge.share_downloads", return_value=[]),
            patch("instagram_mcp.bridge.has_audio", return_value=True),
            patch("instagram_mcp.bridge.to_m4a", return_value=tmp_path / "v.m4a"),
        ):
            result = await g.transcribe_share("t1", "m1")
        g.client.open_share.assert_called_once()
        assert result["transcripts"][0]["text"] == "hallo"

    async def test_a_photo_post_has_nothing_to_transcribe(self, tmp_path: Path) -> None:
        g = TestOpenShare._gateway(tmp_path, [tmp_path / "p.jpg"])
        with (
            patch("instagram_mcp.bridge.share_downloads", return_value=[]),
            pytest.raises(InstagramClientError, match="no video"),
        ):
            await g.transcribe_share("t1", "m1")
