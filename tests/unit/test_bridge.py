"""Unit tests for the bridge's event/JSON serialization (no network)."""

from __future__ import annotations

import time
from datetime import datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import uvicorn

from instagram_mcp import bridge
from instagram_mcp.bridge import Gateway, _msg_json, _thread_json, event_stream, event_to_dict
from instagram_mcp.mqtt.events import (
    Event,
    MessageEvent,
    ReactionEvent,
    SeenEvent,
    ThreadEvent,
    TypingEvent,
    UnsendEvent,
)

if TYPE_CHECKING:
    import pytest


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
            "media_path": None,
            "transcript": None,
            "media_error": None,
            "view_mode": None,
        }

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
