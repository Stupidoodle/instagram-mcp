"""Event router that delivers MQTT events to per-thread subscribers.

Each tool call (e.g. wait_for_reply) subscribes to a specific thread_id.
When the reader thread delivers events, they go only to subscribers of that
thread — other threads see nothing.

A per-thread **replay buffer** catches events that arrive between tool calls
(e.g. between send_and_check finishing and the next subscribe).  When a new
subscriber joins, buffered events are drained into its queue immediately.
"""

from __future__ import annotations

import collections
import contextlib
import logging
import queue
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from instagram_mcp.mqtt.events import Event

logger = logging.getLogger("instagram_mcp.mqtt")

# Replay buffer limits
_BUFFER_MAX_EVENTS = 50
_BUFFER_MAX_AGE_SECONDS = 60


class EventRouter:
    """Routes MQTT events to per-thread-id subscriber queues.

    When no subscriber is active for a thread, events are buffered (up to
    50 events / 60 seconds).  The next ``subscribe()`` call drains the
    buffer so the new subscriber sees anything it missed.
    """

    def __init__(self) -> None:
        self._waiters: dict[str, list[queue.SimpleQueue[Event]]] = {}
        # Replay buffer: thread_id → deque of (timestamp, event)
        self._buffer: dict[str, collections.deque[tuple[float, Event]]] = {}
        self._lock = threading.Lock()

    def subscribe(self, thread_id: str) -> queue.SimpleQueue[Event]:
        """Register a new subscriber for a thread.

        Returns a SimpleQueue that will receive events for this thread_id.
        Any buffered events (from the gap between unsubscribe and now) are
        drained into the queue immediately.
        """
        q: queue.SimpleQueue[Event] = queue.SimpleQueue()
        with self._lock:
            self._waiters.setdefault(thread_id, []).append(q)
            # Drain replay buffer into the new subscriber
            buf = self._buffer.pop(thread_id, None)
            if buf:
                now = time.monotonic()
                for ts, event in buf:
                    if now - ts <= _BUFFER_MAX_AGE_SECONDS:
                        q.put(event)
                logger.info(
                    "Drained %d buffered event(s) for thread %s",
                    len(buf),
                    thread_id,
                )
        return q

    def unsubscribe(self, thread_id: str, q: queue.SimpleQueue[Event]) -> None:
        """Remove a subscriber queue for a thread."""
        with self._lock:
            waiters = self._waiters.get(thread_id, [])
            with contextlib.suppress(ValueError):
                waiters.remove(q)
            if not waiters:
                self._waiters.pop(thread_id, None)

    def deliver(self, event: Event) -> int:
        """Deliver an event to all subscribers of its thread_id.

        If no subscriber exists, the event is stored in the replay buffer
        so the next subscribe() call picks it up.

        Returns the number of subscribers that received the event.
        """
        with self._lock:
            waiters = self._waiters.get(event.thread_id, [])
            if waiters:
                for w in waiters:
                    w.put(event)
                return len(waiters)

            # No subscribers — buffer the event for replay
            buf = self._buffer.setdefault(
                event.thread_id,
                collections.deque(maxlen=_BUFFER_MAX_EVENTS),
            )
            buf.append((time.monotonic(), event))
            return 0

    @property
    def active_threads(self) -> list[str]:
        """Return list of thread_ids with active subscribers."""
        with self._lock:
            return list(self._waiters.keys())
