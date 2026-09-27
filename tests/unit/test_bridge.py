"""Unit tests for the bridge's event/JSON serialization (no network)."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from instagram_mcp.bridge import _msg_json, _thread_json, event_to_dict
from instagram_mcp.mqtt.events import (
    Event,
    MessageEvent,
    ReactionEvent,
    SeenEvent,
    ThreadEvent,
    TypingEvent,
    UnsendEvent,
)


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

    def test_msg_json(self) -> None:
        msg = SimpleNamespace(
            message_id="m1",
            sender=SimpleNamespace(user_id="1", username="a"),
            content=SimpleNamespace(
                text="hi", media_type=SimpleNamespace(value="text"), media_url=None
            ),
            timestamp=datetime(2024, 1, 15, 10, 30, 0),
            is_sent_by_viewer=True,
            seen_since=5,
        )
        data = _msg_json(msg)
        assert data == {
            "message_id": "m1",
            "user_id": "1",
            "username": "a",
            "text": "hi",
            "media_type": "text",
            "media_url": None,
            "timestamp": "2024-01-15T10:30:00",
            "is_from_me": True,
            "seen_since": 5,
        }
