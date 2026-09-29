"""Event dataclasses for MQTT realtime events.

All events are parsed from Instagram's Iris patch format (topic 146)
or Skywalker pubsub (topic 88) and routed by thread_id.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from instagram_mcp.shares import Share


@dataclass(frozen=True)
class Event:
    """Base class for all MQTT events."""

    thread_id: str


@dataclass(frozen=True)
class MessageEvent(Event):
    """New message (topic 146, op=add on items path) or an edit (op=replace)."""

    item_id: str
    user_id: int
    text: str | None
    item_type: str
    timestamp: int  # microseconds since epoch
    edited: bool = False
    link_url: str | None = None  # the shared URL, for item_type "link"
    link_title: str | None = None  # its preview title
    share: Share | None = None  # a shared reel, post, story or profile
    media_path: str | None = None  # downloaded photo/video/voice file (inbound media)
    transcript: str | None = None  # voice notes
    media_error: str | None = None  # why the download or transcription failed
    view_mode: str | None = None  # disappearing photos: "once", "replayable" or "permanent"
    backfilled: bool = False  # filled in from history after the session missed it


@dataclass(frozen=True)
class SeenEvent(Event):
    """Read receipt — someone saw a message (topic 146, op=replace on has_seen path)."""

    user_id: int
    item_id: str
    timestamp: int


@dataclass(frozen=True)
class ReactionEvent(Event):
    """Reaction added or removed (topic 146, op=add/remove on reactions path)."""

    item_id: str
    user_id: int
    reaction_type: str  # "likes" or "emojis"
    emoji: str | None  # None when reaction removed


@dataclass(frozen=True)
class UnsendEvent(Event):
    """Message unsent/deleted (topic 146, op=remove on items path)."""

    item_id: str
    user_id: int


@dataclass(frozen=True)
class TypingEvent(Event):
    """Typing indicator (topic 88 /pubsub via Skywalker)."""

    user_id: int
    activity_status: int  # 0=OFF, 1=TEXT, 2=VISUAL
    ttl: int  # milliseconds until indicator expires


@dataclass(frozen=True)
class ThreadEvent(Event):
    """Generic thread-level event for operations we don't specifically handle."""

    op: str
    path: str
