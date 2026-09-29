"""Catching up on events a session missed while it was offline or reconnecting.

The thin client remembers the last event it delivered and the newest message it saw
per chat. On reconnect it asks the bridge to replay from there; when the bridge can't
(it restarted, or the gap is older than its replay buffer), it fills in from the
chat history: messages newer than the last one seen go out marked ``backfilled``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from instagram_mcp.mqtt.events import MessageEvent
from instagram_mcp.shares import Share

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from instagram_mcp.mqtt.events import Event

logger = logging.getLogger("instagram_mcp.catch_up")

# History media types (the bridge's /messages) -> the raw item types live events use.
_ITEM_TYPES = {
    "text": "text",
    "photo": "media",
    "video": "media",
    "voice": "voice_media",
    "link": "link",
    "raven_media": "raven_media",
    "animated_media": "animated_media",
    "like": "like",
    "media_share": "media_share",
    "reel_share": "clip",
    "story_share": "story_share",
    "xma": "xma",
    "profile": "profile",
}
_RECENT = 500


def state_path(threads: Iterable[str], folder: str | None = None) -> Path:
    """Default state file, one per persona folder and set of subscribed chats."""
    where = folder or os.environ.get("PWD") or str(Path.cwd())
    key = hashlib.sha256("\n".join([where, *sorted(threads)]).encode()).hexdigest()[:12]
    base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return base / "instagram-mcp" / f"channel-{key}.json"


class ChannelState:
    """The last delivered event id and the newest message id per chat, on disk."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()  # recorded from the send loop, read by the stream
        data = self._load()
        self.last_event_id: str | None = data.get("last_event_id")
        self._items: dict[str, str] = dict(data.get("threads") or {})

    def last_item(self, thread_id: str) -> str | None:
        """The newest message id seen in a chat, if any."""
        return self._items.get(thread_id)

    def record(
        self, *, event_id: str | None = None, thread_id: str | None = None, item_id: str = ""
    ) -> None:
        """Remember an event id and/or a message; never moves back, saves on change."""
        with self._lock:
            changed = False
            if event_id and _later(event_id, self.last_event_id):
                self.last_event_id, changed = event_id, True
            if thread_id and order(item_id) > order(self._items.get(thread_id, "")):
                self._items[thread_id], changed = item_id, True
            if changed:
                self._save()

    def _load(self) -> dict[str, Any]:
        try:
            data: dict[str, Any] = json.loads(self.path.read_text())
        except OSError, ValueError:
            return {}
        return data

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"last_event_id": self.last_event_id, "threads": self._items}))
        tmp.replace(self.path)


class CatchUp:
    """Deduplicates delivery and backfills from history when the bridge reports a gap.

    ``sink`` is the channel's ``handle(event, on_sent)``: the saved position only moves
    once a notification is actually written to Claude Code, so anything the session
    never got (it died before attaching, say) is replayed next time.
    """

    def __init__(
        self,
        state: ChannelState,
        history: Callable[[str, int], list[dict[str, Any]]],
        threads: Callable[[], list[str]],
        sink: Callable[[Event, Callable[[], None]], None],
        amount: int = 30,
    ) -> None:
        self._state = state
        self._history = history
        self._threads = threads
        self._sink = sink
        self._amount = amount
        self._recent: deque[str] = deque(maxlen=_RECENT)

    def last_event_id(self) -> str | None:
        """What to send as Last-Event-ID."""
        return self._state.last_event_id

    def deliver(self, event: Event, event_id: str | None) -> None:
        """Pass an event on unless its message was already delivered."""
        message = event if isinstance(event, MessageEvent) and not event.edited else None
        if message is not None:
            if message.item_id in self._recent:
                return
            self._recent.append(message.item_id)

        def sent() -> None:
            if message is None:
                self._state.record(event_id=event_id)
            else:
                self._state.record(
                    event_id=event_id, thread_id=message.thread_id, item_id=message.item_id
                )

        self._sink(event, sent)

    def fill_gap(self) -> int:
        """Deliver messages newer than the last one seen, per subscribed chat."""
        sent = 0
        for thread_id in self._threads():
            try:
                items = self._history(thread_id, self._amount)
            except Exception:
                logger.warning("Could not catch up on …%s", thread_id[-4:], exc_info=True)
                continue
            events = sorted(
                (e for item in items if (e := history_event(thread_id, item)) is not None),
                key=lambda e: order(e.item_id),
            )
            last = self._state.last_item(thread_id)
            if last is None:  # first run: nothing to catch up on, just mark the spot
                if events:
                    self._state.record(thread_id=thread_id, item_id=events[-1].item_id)
                continue
            for event in events:
                if order(event.item_id) > order(last) and event.item_id not in self._recent:
                    self.deliver(event, None)
                    sent += 1
        if sent:
            logger.info("Caught up on %d message(s) from history", sent)
        return sent


def history_event(thread_id: str, item: dict[str, Any]) -> MessageEvent | None:
    """A history message as a (backfilled) message event, or None for system items."""
    item_type = _ITEM_TYPES.get(item.get("media_type") or "")
    if item_type is None:
        return None
    at = datetime.fromisoformat(str(item["timestamp"]))
    user = str(item.get("user_id") or "0")
    return MessageEvent(
        thread_id,
        str(item["message_id"]),
        int(user) if user.isdigit() else 0,
        item.get("text"),
        item_type,
        int(at.astimezone().timestamp() * 1_000_000),
        link_url=item.get("link_url"),
        link_title=item.get("link_title"),
        share=Share.from_dict(item.get("share")),
        backfilled=True,
    )


def _later(event_id: str, current: str | None) -> bool:
    """Whether ``event_id`` comes after ``current`` (a new bridge boot always does)."""
    boot, _, seq = event_id.rpartition("-")
    old_boot, _, old_seq = (current or "").rpartition("-")
    if boot != old_boot or not old_seq.isdigit():
        return True
    return seq.isdigit() and int(seq) > int(old_seq)


def order(item_id: str) -> int:
    """Instagram item ids grow over time; non-numeric ids sort first."""
    return int(item_id) if item_id.isdigit() else -1
