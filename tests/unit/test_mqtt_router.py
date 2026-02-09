"""Unit tests for MQTT EventRouter."""

import queue

from instagram_mcp.mqtt.events import MessageEvent, SeenEvent, TypingEvent
from instagram_mcp.mqtt.router import EventRouter


class TestEventRouter:
    def test_subscribe_returns_queue(self) -> None:
        router = EventRouter()
        q = router.subscribe("thread1")
        assert isinstance(q, queue.SimpleQueue)
        router.unsubscribe("thread1", q)

    def test_deliver_to_subscriber(self) -> None:
        router = EventRouter()
        q = router.subscribe("T1")
        event = MessageEvent(
            thread_id="T1",
            item_id="I1",
            user_id=1,
            text="hello",
            item_type="text",
            timestamp=0,
        )
        delivered = router.deliver(event)
        assert delivered == 1
        assert q.get_nowait() is event
        router.unsubscribe("T1", q)

    def test_deliver_no_subscribers(self) -> None:
        router = EventRouter()
        event = MessageEvent(
            thread_id="T1",
            item_id="I1",
            user_id=1,
            text="hello",
            item_type="text",
            timestamp=0,
        )
        delivered = router.deliver(event)
        assert delivered == 0

    def test_deliver_wrong_thread(self) -> None:
        """Events for thread T2 should not reach subscriber of T1."""
        router = EventRouter()
        q = router.subscribe("T1")
        event = MessageEvent(
            thread_id="T2",
            item_id="I1",
            user_id=1,
            text="hello",
            item_type="text",
            timestamp=0,
        )
        router.deliver(event)
        assert q.empty()
        router.unsubscribe("T1", q)

    def test_multiple_subscribers_same_thread(self) -> None:
        router = EventRouter()
        q1 = router.subscribe("T1")
        q2 = router.subscribe("T1")
        event = MessageEvent(
            thread_id="T1",
            item_id="I1",
            user_id=1,
            text="hello",
            item_type="text",
            timestamp=0,
        )
        delivered = router.deliver(event)
        assert delivered == 2
        assert q1.get_nowait() is event
        assert q2.get_nowait() is event
        router.unsubscribe("T1", q1)
        router.unsubscribe("T1", q2)

    def test_unsubscribe_removes_queue(self) -> None:
        router = EventRouter()
        q = router.subscribe("T1")
        router.unsubscribe("T1", q)
        event = MessageEvent(
            thread_id="T1",
            item_id="I1",
            user_id=1,
            text="hello",
            item_type="text",
            timestamp=0,
        )
        delivered = router.deliver(event)
        assert delivered == 0

    def test_unsubscribe_nonexistent_thread(self) -> None:
        """Unsubscribing from a thread with no subscribers should not raise."""
        router = EventRouter()
        q: queue.SimpleQueue[object] = queue.SimpleQueue()
        router.unsubscribe("nonexistent", q)  # type: ignore[arg-type]

    def test_unsubscribe_wrong_queue(self) -> None:
        """Unsubscribing a queue that isn't registered should not raise."""
        router = EventRouter()
        q1 = router.subscribe("T1")
        q2: queue.SimpleQueue[object] = queue.SimpleQueue()
        router.unsubscribe("T1", q2)  # type: ignore[arg-type]
        # q1 should still be subscribed
        event = MessageEvent(
            thread_id="T1",
            item_id="I1",
            user_id=1,
            text="hello",
            item_type="text",
            timestamp=0,
        )
        assert router.deliver(event) == 1
        router.unsubscribe("T1", q1)

    def test_active_threads(self) -> None:
        router = EventRouter()
        assert router.active_threads == []
        q1 = router.subscribe("T1")
        q2 = router.subscribe("T2")
        threads = router.active_threads
        assert sorted(threads) == ["T1", "T2"]
        router.unsubscribe("T1", q1)
        assert router.active_threads == ["T2"]
        router.unsubscribe("T2", q2)
        assert router.active_threads == []

    def test_delivers_multiple_event_types(self) -> None:
        """Router delivers all event types, not just MessageEvent."""
        router = EventRouter()
        q = router.subscribe("T1")

        msg = MessageEvent(
            thread_id="T1",
            item_id="I1",
            user_id=1,
            text="hi",
            item_type="text",
            timestamp=0,
        )
        seen = SeenEvent(thread_id="T1", user_id=2, item_id="I1", timestamp=100)
        typing = TypingEvent(thread_id="T1", user_id=2, activity_status=1, ttl=5000)

        router.deliver(msg)
        router.deliver(seen)
        router.deliver(typing)

        assert q.get_nowait() is msg
        assert q.get_nowait() is seen
        assert q.get_nowait() is typing
        router.unsubscribe("T1", q)

    def test_unsubscribe_cleans_up_empty_thread(self) -> None:
        """After all subscribers leave, thread_id is removed from waiters."""
        router = EventRouter()
        q = router.subscribe("T1")
        assert "T1" in router.active_threads
        router.unsubscribe("T1", q)
        assert "T1" not in router.active_threads


class TestEventRouterReplayBuffer:
    """Tests for the replay buffer that bridges gaps between tool calls."""

    def _msg(self, thread_id: str = "T1", text: str = "hi") -> MessageEvent:
        return MessageEvent(
            thread_id=thread_id,
            item_id="I1",
            user_id=1,
            text=text,
            item_type="text",
            timestamp=0,
        )

    def test_buffered_event_replayed_on_subscribe(self) -> None:
        """Events delivered with no subscriber are replayed on next subscribe."""
        router = EventRouter()
        event = self._msg(text="missed")

        # No subscriber — event goes to buffer
        assert router.deliver(event) == 0

        # New subscriber gets the buffered event
        q = router.subscribe("T1")
        assert q.get_nowait() is event
        router.unsubscribe("T1", q)

    def test_buffer_drained_after_unsubscribe_resubscribe(self) -> None:
        """Events arriving between unsubscribe and resubscribe are captured."""
        router = EventRouter()

        # First subscriber
        q1 = router.subscribe("T1")
        router.unsubscribe("T1", q1)

        # Event arrives during the gap
        gap_event = self._msg(text="during gap")
        assert router.deliver(gap_event) == 0

        # Resubscribe — should get the gap event
        q2 = router.subscribe("T1")
        assert q2.get_nowait() is gap_event
        router.unsubscribe("T1", q2)

    def test_buffer_cleared_after_drain(self) -> None:
        """Buffer is emptied after draining into a subscriber."""
        router = EventRouter()
        router.deliver(self._msg(text="buffered"))

        q1 = router.subscribe("T1")
        q1.get_nowait()  # drain
        router.unsubscribe("T1", q1)

        # Second subscribe should NOT see the old event
        q2 = router.subscribe("T1")
        assert q2.empty()
        router.unsubscribe("T1", q2)

    def test_buffer_not_used_when_subscriber_exists(self) -> None:
        """Events go directly to subscribers, not the buffer."""
        router = EventRouter()
        q = router.subscribe("T1")

        event = self._msg()
        delivered = router.deliver(event)
        assert delivered == 1
        assert q.get_nowait() is event

        router.unsubscribe("T1", q)

        # Resubscribe — buffer should be empty
        q2 = router.subscribe("T1")
        assert q2.empty()
        router.unsubscribe("T1", q2)

    def test_buffer_wrong_thread_not_replayed(self) -> None:
        """Buffered events for T2 are not replayed to T1 subscriber."""
        router = EventRouter()
        router.deliver(self._msg(thread_id="T2", text="for T2"))

        q = router.subscribe("T1")
        assert q.empty()
        router.unsubscribe("T1", q)

    def test_buffer_multiple_events(self) -> None:
        """Multiple buffered events are replayed in order."""
        router = EventRouter()
        e1 = self._msg(text="first")
        e2 = self._msg(text="second")
        router.deliver(e1)
        router.deliver(e2)

        q = router.subscribe("T1")
        assert q.get_nowait() is e1
        assert q.get_nowait() is e2
        router.unsubscribe("T1", q)
