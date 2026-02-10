"""Parse MQTT payloads into typed Event objects.

Handles topic 146 (/ig_message_sync) Iris patches and topic 88 (/pubsub)
Skywalker typing indicators.
"""

from __future__ import annotations

import json
import logging
import struct
import zlib
from typing import Any

from instagram_mcp.mqtt.events import (
    Event,
    MessageEvent,
    ReactionEvent,
    SeenEvent,
    ThreadEvent,
    TypingEvent,
    UnsendEvent,
)
from instagram_mcp.mqtt.topics import MESSAGE_SYNC, PUBSUB, TOPIC_NAMES

logger = logging.getLogger("instagram_mcp.mqtt")


def parse_publish_packet(first_byte: int, body: bytes) -> tuple[str, int | None, bytes]:
    """Extract topic, packet_id, and payload from a PUBLISH packet body.

    Args:
        first_byte: The first byte of the MQTT packet (contains QoS flags).
        body: The packet body after the fixed header.

    Returns:
        Tuple of (topic_string, packet_id_or_None, payload_bytes).
    """
    idx = 0
    topic_len = struct.unpack("!H", body[idx : idx + 2])[0]
    topic = body[idx + 2 : idx + 2 + topic_len].decode("utf-8", errors="replace")
    payload_start = idx + 2 + topic_len

    qos = (first_byte >> 1) & 0x03
    packet_id = None
    if qos > 0:
        packet_id = struct.unpack("!H", body[payload_start : payload_start + 2])[0]
        payload_start += 2

    return topic, packet_id, body[payload_start:]


def parse_payload(topic: str, raw_payload: bytes) -> tuple[list[Event], int]:
    """Parse a raw MQTT payload into typed Event objects.

    Args:
        topic: The topic string (e.g. "146" for /ig_message_sync).
        raw_payload: The raw (possibly zlib-compressed) payload bytes.

    Returns:
        Tuple of (events, max_seq_id). max_seq_id is the highest Iris
        sequence ID seen in this payload (0 if none / not Iris topic).
    """
    topic_int = int(topic) if topic.isdigit() else -1
    topic_name = TOPIC_NAMES.get(topic, topic)

    # Only process topics we handle; skip the rest silently.
    if topic_int not in (MESSAGE_SYNC, PUBSUB):
        return [], 0

    try:
        decompressed = zlib.decompress(raw_payload)
        text = decompressed.decode("utf-8", errors="replace")
        data = json.loads(text)
    except Exception:
        logger.warning(
            "Could not decompress/parse payload on %s (%dB)",
            topic_name,
            len(raw_payload),
        )
        return [], 0

    if topic_int == MESSAGE_SYNC:
        return _parse_iris_payload(data)
    return _parse_pubsub_payload(data), 0


def _parse_iris_payload(data: Any) -> tuple[list[Event], int]:
    """Parse topic 146 Iris patch payload into events.

    The payload is a JSON array of patch objects:
    [{
        "event": "patch",
        "data": [{"op": "add", "path": "/direct_v2/threads/{tid}/items/{iid}", "value": "..."}],
        "seq_id": 12345,
        "realtime": true
    }]

    Returns:
        Tuple of (events, max_seq_id). max_seq_id is the highest seq_id
        seen across all sync items, used to advance the Iris subscription
        cursor on reconnect.
    """
    events: list[Event] = []
    max_seq_id = 0

    if not isinstance(data, list):
        data = [data]

    for sync_item in data:
        if not isinstance(sync_item, dict):
            continue

        # Track the highest seq_id for reconnection
        seq_id = sync_item.get("seq_id", 0)
        if isinstance(seq_id, int) and seq_id > max_seq_id:
            max_seq_id = seq_id

        for patch in sync_item.get("data", []):
            if not isinstance(patch, dict):
                continue

            op = patch.get("op", "")
            path = patch.get("path", "")
            value_str = patch.get("value", "{}")

            parsed = _parse_iris_patch(op, path, value_str)
            if parsed:
                events.append(parsed)

    return events, max_seq_id


def _parse_iris_patch(op: str, path: str, value_str: str) -> Event | None:  # noqa: PLR0911
    """Parse a single Iris patch operation into an Event.

    Routes by path pattern:
    - /direct_v2/threads/{tid}/items/{iid} → Message or Unsend
    - /direct_v2/threads/{tid}/participants/{uid}/has_seen → Seen
    - /direct_v2/threads/{tid}/items/{iid}/reactions/{type}/{uid} → Reaction
    - /direct_v2/inbox/threads/{tid} → Thread
    """
    parts = path.split("/")
    # parts[0] is empty string (leading /)

    # /direct_v2/threads/{tid}/...
    if len(parts) >= 5 and parts[1] == "direct_v2" and parts[2] == "threads":
        thread_id = parts[3]
        subpath = parts[4] if len(parts) > 4 else ""

        # /direct_v2/threads/{tid}/items/{iid}
        if subpath == "items" and len(parts) >= 6:
            item_id = parts[5]

            # Check for reaction subpath
            # /direct_v2/threads/{tid}/items/{iid}/reactions/{type}/{uid}
            if len(parts) >= 9 and parts[6] == "reactions":
                return _parse_reaction(thread_id, item_id, op, parts, value_str)

            # Message add or unsend
            if op == "add":
                return _parse_message(thread_id, item_id, value_str)
            elif op == "remove":
                return _parse_unsend(thread_id, item_id, value_str)
            elif op == "replace":
                # Message update (edit, etc.) — treat as message
                return _parse_message(thread_id, item_id, value_str)

        # /direct_v2/threads/{tid}/participants/{uid}/has_seen
        if subpath == "participants" and len(parts) >= 7 and parts[6] == "has_seen":
            user_id_str = parts[5]
            return _parse_seen(thread_id, user_id_str, value_str)

        # /direct_v2/threads/{tid}/activity_indicator_id/...
        if subpath == "activity_indicator_id":
            return _parse_activity_indicator(thread_id, value_str)

        # Generic thread event for anything else
        return ThreadEvent(thread_id=thread_id, op=op, path=path)

    # /direct_v2/inbox/threads/{tid}
    if (
        len(parts) >= 5
        and parts[1] == "direct_v2"
        and parts[2] == "inbox"
        and parts[3] == "threads"
    ):
        thread_id = parts[4]
        return ThreadEvent(thread_id=thread_id, op=op, path=path)

    return None


def _parse_message(thread_id: str, item_id: str, value_str: str) -> MessageEvent | None:
    """Parse a message add/replace event."""
    try:
        value = json.loads(value_str) if isinstance(value_str, str) else value_str
    except (json.JSONDecodeError, TypeError):
        return None

    if not isinstance(value, dict):
        return None

    return MessageEvent(
        thread_id=thread_id,
        item_id=value.get("item_id", item_id),
        user_id=int(value.get("user_id", 0)),
        text=value.get("text"),
        item_type=value.get("item_type", "unknown"),
        timestamp=int(value.get("timestamp", 0)),
    )


def _parse_unsend(thread_id: str, item_id: str, value_str: str) -> UnsendEvent:
    """Parse a message remove (unsend) event."""
    user_id = 0
    try:
        value = json.loads(value_str) if isinstance(value_str, str) else value_str
        if isinstance(value, dict):
            user_id = int(value.get("user_id", 0))
    except (json.JSONDecodeError, TypeError, ValueError):
        pass

    return UnsendEvent(
        thread_id=thread_id,
        item_id=item_id,
        user_id=user_id,
    )


def _parse_seen(thread_id: str, user_id_str: str, value_str: str) -> SeenEvent | None:
    """Parse a read receipt (has_seen) event."""
    try:
        value = json.loads(value_str) if isinstance(value_str, str) else value_str
        user_id = int(user_id_str)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None

    item_id = ""
    timestamp = 0
    if isinstance(value, dict):
        item_id = str(value.get("item_id", ""))
        timestamp = int(value.get("timestamp", 0))

    return SeenEvent(
        thread_id=thread_id,
        user_id=user_id,
        item_id=item_id,
        timestamp=timestamp,
    )


def _parse_reaction(
    thread_id: str,
    item_id: str,
    op: str,
    parts: list[str],
    value_str: str,
) -> ReactionEvent | None:
    """Parse a reaction add/remove event."""
    if len(parts) < 9:
        return None

    reaction_type = parts[7]  # "likes" or "emojis"
    user_id_str = parts[8]

    emoji = None
    if op != "remove":
        try:
            value = json.loads(value_str) if isinstance(value_str, str) else value_str
            if isinstance(value, dict):
                emoji = value.get("emoji")
        except (json.JSONDecodeError, TypeError):
            pass

    try:
        user_id = int(user_id_str)
    except ValueError:
        return None

    return ReactionEvent(
        thread_id=thread_id,
        item_id=item_id,
        user_id=user_id,
        reaction_type=reaction_type,
        emoji=emoji,
    )


def _parse_activity_indicator(thread_id: str, value_str: str) -> TypingEvent | None:
    """Parse a typing indicator from topic 146 activity_indicator path."""
    try:
        value = json.loads(value_str) if isinstance(value_str, str) else value_str
    except (json.JSONDecodeError, TypeError):
        return None

    if not isinstance(value, dict):
        return None

    return TypingEvent(
        thread_id=thread_id,
        user_id=int(value.get("sender_id", 0)),
        activity_status=int(value.get("activity_status", 0)),
        ttl=int(value.get("ttl", 0)),
    )


def _parse_pubsub_payload(data: Any) -> list[Event]:
    """Parse topic 88 (/pubsub) Skywalker typing indicators.

    These arrive as JSON with activity indicator data and a path
    that includes the thread_id.
    """
    events: list[Event] = []

    if not isinstance(data, dict) and not isinstance(data, list):
        return events

    items = data if isinstance(data, list) else [data]

    for item in items:
        if not isinstance(item, dict):
            continue

        # Pubsub events may have different formats
        # Try to extract typing indicator data
        path = item.get("path", "")
        value = item.get("value", item)

        # Extract thread_id from path if present
        parts = path.split("/") if path else []
        thread_id = ""
        if len(parts) >= 4 and parts[2] == "threads":
            thread_id = parts[3]

        # Look for typing indicator fields
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                continue

        if isinstance(value, dict) and "sender_id" in value:
            event = TypingEvent(
                thread_id=thread_id or str(value.get("thread_id", "")),
                user_id=int(value.get("sender_id", 0)),
                activity_status=int(value.get("activity_status", 0)),
                ttl=int(value.get("ttl", 0)),
            )
            events.append(event)

    return events
