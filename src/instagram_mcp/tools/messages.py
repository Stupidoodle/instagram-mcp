"""Message operation tools for Instagram MCP Server.

This module provides MCP tools for sending, receiving, and managing
Instagram Direct Messages.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from instagram_mcp.models.schemas import MediaType

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from instagram_mcp.client import InstagramClient

logger = logging.getLogger("instagram_mcp")


def _get_mqtt_manager() -> Any:
    """Lazy import to avoid circular dependency."""
    from instagram_mcp.server import get_mqtt_manager

    return get_mqtt_manager()


def _effective_timeout_seconds(timeout_minutes: int) -> int:
    """Return the effective wait budget in seconds."""
    return timeout_minutes * 60


# Cache: thread_id → {user_id_str: username}
_thread_users: dict[str, dict[str, str]] = {}


def _get_user_map(client: Any, thread_id: str) -> dict[str, str]:
    """Get user_id→username mapping for a thread (cached, 1 REST call)."""
    if thread_id not in _thread_users:
        try:
            thread = client.get_thread(thread_id)
            _thread_users[thread_id] = {u.user_id: u.username for u in thread.users}
        except Exception:
            _thread_users[thread_id] = {}
    return _thread_users[thread_id]


def _username(user_map: dict[str, str], user_id: int | str) -> str:
    """Resolve user_id to username, falling back to the raw ID."""
    return user_map.get(str(user_id), str(user_id))


def _wait_for_reply_mqtt(  # noqa: PLR0912
    mqtt: Any,
    thread_id: str,
    timeout_minutes: int,
    double_text_grace_period_seconds: int,
    self_user_id: str = "",
    user_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    """MQTT fast path for wait_for_reply. Zero REST calls.

    Collects ALL event types during the wait — messages, typing indicators,
    read receipts, reactions, unsends — and returns them as rich metadata
    alongside the messages.  Filters out self-echoes (own messages pushed
    back by Instagram MQTT).
    """
    _umap = user_map or {}
    import queue as queue_mod

    from instagram_mcp.mqtt.events import (
        MessageEvent,
        ReactionEvent,
        SeenEvent,
        TypingEvent,
        UnsendEvent,
    )

    start_time = time.time()
    timeout_seconds = _effective_timeout_seconds(timeout_minutes)
    deadline = start_time + timeout_seconds

    logger.info(
        "wait_for_reply MQTT: subscribing to thread %s (budget=%ds, mqtt_alive=%s)",
        thread_id,
        timeout_seconds,
        mqtt.is_connected,
    )

    # Subscribe to ALL events for this thread
    q = mqtt.router.subscribe(thread_id)

    messages: list[MessageEvent] = []
    seen_events: list[SeenEvent] = []
    typing_events: list[TypingEvent] = []
    reactions: list[ReactionEvent] = []
    unsends: list[UnsendEvent] = []
    first_message_time: float | None = None

    try:
        while time.time() < deadline:
            remaining = deadline - time.time()
            if remaining <= 0:
                break

            # If we have messages and grace period expired, we're done
            if (
                first_message_time is not None
                and time.time() - first_message_time >= double_text_grace_period_seconds
            ):
                break

            try:
                event = q.get(timeout=min(remaining, 0.5))
            except queue_mod.Empty:
                if not mqtt.is_connected:
                    break
                continue

            # Collect by event type (skip self-echoes)
            if isinstance(event, MessageEvent):
                if self_user_id and str(event.user_id) == self_user_id:
                    continue  # own message echoed back by MQTT
                messages.append(event)
                if first_message_time is None:
                    first_message_time = time.time()
                    logger.info(
                        "MQTT: message received, collecting for %ds...",
                        double_text_grace_period_seconds,
                    )
            elif isinstance(event, SeenEvent):
                seen_events.append(event)
            elif isinstance(event, TypingEvent):
                typing_events.append(event)
            elif isinstance(event, ReactionEvent):
                reactions.append(event)
            elif isinstance(event, UnsendEvent):
                unsends.append(event)
    finally:
        mqtt.router.unsubscribe(thread_id, q)

    waited = int(time.time() - start_time)

    if not messages:
        result: dict[str, Any] = {
            "timeout": True,
            "waited_seconds": waited,
        }
    else:
        msg_list = []
        for m in messages:
            d: dict[str, Any] = {"sender": _username(_umap, m.user_id), "text": m.text}
            if m.item_type and m.item_type != "text":
                d["media_type"] = m.item_type
            msg_list.append(d)

        result = {
            "success": True,
            "new_messages": msg_list,
            "waited_seconds": waited,
        }

    # Compact MQTT metadata
    if seen_events:
        result["seen"] = True
    if typing_events:
        result["typing"] = True
    if reactions:
        result["reactions"] = [
            {"emoji": e.emoji, "item_id": e.item_id} for e in reactions
        ]
    if unsends:
        result["unsent"] = [e.item_id for e in unsends]

    return result


def _wait_for_reply_rest(  # noqa: PLR0912
    client: Any,
    thread_id: str,
    timeout_minutes: int,
    poll_interval_seconds: int,
    double_text_grace_period_seconds: int,
) -> dict[str, Any]:
    """REST polling fallback for wait_for_reply."""
    # SYNC PHASE: Poll until our latest message is visible
    sync_start = time.time()
    baseline_timestamp = None
    sync_attempts = 0

    while time.time() - sync_start < 30:
        sync_attempts += 1
        current_messages = client.get_messages(thread_id=thread_id, amount=10)

        for msg in current_messages:
            if msg.is_sent_by_viewer:
                baseline_timestamp = msg.timestamp
                break

        if baseline_timestamp:
            break
        time.sleep(1)

    logger.info(
        "Waiting for reply in thread %s (baseline: %s, synced in %d attempts)",
        thread_id,
        baseline_timestamp,
        sync_attempts,
    )

    timeout_seconds = _effective_timeout_seconds(timeout_minutes)
    start_time = time.time()
    first_new_message_time = None

    while True:
        elapsed = time.time() - start_time

        if elapsed >= timeout_seconds:
            return {
                "timeout": True,
                "thread_id": thread_id,
                "waited_minutes": int(elapsed / 60),
                "waited_seconds": int(elapsed),
            }

        messages = client.get_messages(thread_id=thread_id, amount=10)

        new_messages = []
        for msg in messages:
            if msg.is_sent_by_viewer:
                continue
            if baseline_timestamp and msg.timestamp <= baseline_timestamp:
                continue
            if msg.content.media_type == MediaType.ACTION_LOG:
                continue
            new_messages.append(msg)

        if new_messages:
            if first_new_message_time is None:
                first_new_message_time = time.time()
                logger.info(
                    "New message detected, waiting %ds for double texts...",
                    double_text_grace_period_seconds,
                )

            grace_elapsed = time.time() - first_new_message_time
            if grace_elapsed >= double_text_grace_period_seconds:
                new_messages.sort(key=lambda m: m.timestamp)

                msg_list = []
                for m in new_messages:
                    d: dict[str, Any] = {
                        "sender": m.sender.username,
                        "text": m.content.text,
                    }
                    if m.content.media_type.value != "text":
                        d["media_type"] = m.content.media_type.value
                    msg_list.append(d)

                return {
                    "success": True,
                    "new_messages": msg_list,
                    "waited_seconds": int(elapsed),
                }

        time.sleep(poll_interval_seconds)


def _send_and_check_mqtt(
    mqtt: Any,
    q: Any,
    thread_id: str,
    sent_message_id: str,
    self_user_id: str = "",
    user_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    """MQTT fast path for send_and_check. 0 REST calls for interjection check.

    Expects a pre-subscribed queue (subscribed BEFORE the REST send so we
    don't miss interjections during the send latency).  Uses a short 3-second
    base window — if they read or start typing, the deadline extends smartly.
    Filters out self-echoes (own message pushed back by MQTT).
    """
    _umap = user_map or {}
    import queue as queue_mod

    from instagram_mcp.mqtt.events import (
        MessageEvent,
        SeenEvent,
        TypingEvent,
    )

    _BASE_WINDOW = 3  # seconds — enough for MQTT-speed interjection
    _SEEN_GRACE = 2  # extra seconds after they read it
    _TYPING_EXTEND = 5  # extra seconds if typing detected
    _ABSOLUTE_CAP = 15  # never wait longer than this

    now = time.time()
    deadline = now + _BASE_WINDOW
    cap = now + _ABSOLUTE_CAP

    interjection: MessageEvent | None = None
    seen = False
    typing = False

    try:
        while time.time() < deadline:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            try:
                event = q.get(timeout=min(remaining, 0.5))
            except queue_mod.Empty:
                if not mqtt.is_connected:
                    break
                continue

            if isinstance(event, MessageEvent):
                if self_user_id and str(event.user_id) == self_user_id:
                    continue  # own message echoed back by MQTT
                interjection = event
                break
            elif isinstance(event, SeenEvent) and not seen:
                seen = True
                deadline = min(time.time() + _SEEN_GRACE, cap)
            elif isinstance(event, TypingEvent) and not typing:
                typing = True
                deadline = min(time.time() + _TYPING_EXTEND, cap)
    finally:
        mqtt.router.unsubscribe(thread_id, q)

    result: dict[str, Any] = {
        "success": True,
        "has_interjection": interjection is not None,
    }

    if interjection is not None:
        result["interjection"] = {
            "sender": _username(_umap, interjection.user_id),
            "text": interjection.text,
        }
    if seen:
        result["seen"] = True
    if typing:
        result["typing"] = True

    return result


def _send_and_check_rest(
    client: Any,
    thread_id: str,
    sent_message_id: str,
    sync_timeout_seconds: int,
) -> dict[str, Any]:
    """REST polling fallback for send_and_check."""
    # Get baseline BEFORE syncing
    pre_send_messages = client.get_messages(thread_id=thread_id, amount=5)
    baseline_timestamp = None
    for msg in pre_send_messages:
        if not msg.is_sent_by_viewer:
            baseline_timestamp = msg.timestamp
            break

    # Sync: poll until our message is visible
    sync_start = time.time()
    synced = False
    while time.time() - sync_start < sync_timeout_seconds:
        messages = client.get_messages(thread_id=thread_id, amount=10)
        for msg in messages:
            if msg.message_id == sent_message_id:
                synced = True
                break
        if synced:
            break
        time.sleep(0.5)

    if not synced:
        logger.warning("Sync timeout for message %s", sent_message_id)

    # Check for interjections
    post_send_messages = client.get_messages(thread_id=thread_id, amount=10)
    interjections = []
    for msg in post_send_messages:
        if msg.is_sent_by_viewer:
            continue
        if baseline_timestamp and msg.timestamp <= baseline_timestamp:
            continue
        interjections.append(msg)

    result_dict: dict[str, Any] = {
        "success": True,
        "message_id": sent_message_id,
        "has_interjection": len(interjections) > 0,
    }
    if interjections:
        result_dict["interjection"] = {
            "sender": interjections[0].sender.username,
            "text": interjections[0].content.text,
        }

    return result_dict


def register_message_tools(mcp: FastMCP, client: InstagramClient) -> None:
    """Register message operation tools with the MCP server.

    Args:
        mcp: FastMCP server instance.
        client: Instagram client instance.
    """

    @mcp.tool()
    def send_message(
        text: str,
        user_ids: list[str] | None = None,
        thread_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Send a text message to users or existing threads.

        Args:
            text: Message text to send.
            user_ids: List of user IDs to send to (creates new threads if needed).
            thread_ids: List of thread IDs to send to (existing conversations).

        Returns:
            Object with 'success', 'message_id', and 'thread_id' on success,
            or 'error' on failure. Must provide either user_ids or thread_ids.
        """
        if not user_ids and not thread_ids:
            return {"error": "Must specify either user_ids or thread_ids"}

        try:
            message = client.send_message(
                text=text,
                user_ids=user_ids,
                thread_ids=thread_ids,
            )
            if message:
                return {
                    "success": True,
                    "message_id": message.message_id,
                    "thread_id": message.thread_id,
                    "text": text,
                }
            return {"success": False, "error": "Failed to send message"}
        except Exception as e:
            logger.exception("Error sending message")
            return {"error": str(e)}

    @mcp.tool()
    def reply_to_thread(thread_id: str, text: str) -> dict[str, Any]:
        """Reply to an existing conversation thread.

        Args:
            thread_id: ID of the thread to reply to.
            text: Message text to send.

        Returns:
            Object with 'success', 'message_id', and 'thread_id' on success.
        """
        try:
            message = client.reply_to_thread(thread_id=thread_id, text=text)
            if message:
                return {
                    "success": True,
                    "message_id": message.message_id,
                    "thread_id": message.thread_id,
                    "text": text,
                }
            return {"success": False, "error": "Failed to send reply"}
        except Exception as e:
            logger.exception("Error replying to thread %s", thread_id)
            return {"error": str(e), "thread_id": thread_id}

    @mcp.tool(annotations={"destructive": True})
    def send_and_check(
        thread_id: str,
        text: str,
        sync_timeout_seconds: int = 15,
    ) -> dict[str, Any]:
        """Send a message, sync, and check for interjections. Use for natural double-texting.

        This is the preferred tool for sending messages during active conversations.
        It combines three operations atomically:
        1. Send your message
        2. Sync (wait until your message is visible in the thread)
        3. Check if they sent anything while you were typing/sending

        Use this for natural double/triple texting:
        - send_and_check("bro what is that 💀")  -> no interjection, continue
        - send_and_check("where did you get that from")  -> interjection! they said "wait"
        - Now decide: engage with their "wait" or continue your thought

        Args:
            thread_id: ID of the thread to send to.
            text: Message text to send.
            sync_timeout_seconds: Max time to wait for sync (default: 15).

        Returns:
            success: Whether message was sent and synced.
            has_interjection: True if they sent something since you started.
            interjection: Their message if has_interjection, else None.
        """
        mqtt = _get_mqtt_manager()
        q = None
        try:
            # MQTT: subscribe BEFORE sending so we catch interjections
            # that arrive during the 1-2s REST send latency.
            if mqtt and mqtt.is_connected:
                q = mqtt.router.subscribe(thread_id)

            # Send the message (1 REST call, always needed)
            message = client.reply_to_thread(thread_id=thread_id, text=text)
            if not message:
                return {"success": False, "error": "Failed to send message"}

            sent_message_id = message.message_id

            # MQTT FAST PATH: 3s interjection window, 0 REST polls
            if q:
                self_uid = str(client.client.user_id) if client.client.user_id else ""
                umap = _get_user_map(client, thread_id)
                result = _send_and_check_mqtt(
                    mqtt, q, thread_id, sent_message_id,
                    self_user_id=self_uid, user_map=umap,
                )
                q = None  # already unsubscribed inside _send_and_check_mqtt
                return result

            # REST SLOW PATH: polling fallback
            return _send_and_check_rest(
                client, thread_id, sent_message_id, sync_timeout_seconds
            )

        except Exception as e:
            logger.exception("Error in send_and_check for thread %s", thread_id)
            return {"error": str(e)}
        finally:
            # Clean up subscription if MQTT path wasn't taken (error before it)
            if q and mqtt:
                try:
                    mqtt.router.unsubscribe(thread_id, q)
                except Exception:
                    pass

    @mcp.tool()
    def get_messages(thread_id: str, amount: int = 20, offset: int = 0) -> dict[str, Any]:
        """Get messages from a thread.

        Args:
            thread_id: ID of the thread to get messages from.
            amount: Maximum number of messages to fetch (default: 20).
            offset: Skip the N most recent messages (default: 0).
                Use for pagination: offset=0 gets latest, offset=50 gets older.

        Returns:
            Object with 'thread_id', 'messages' array containing objects with
            sender, text, media_type, timestamp, and seen_since (when available).
        """
        try:
            fetch_total = offset + amount
            all_messages = client.get_messages(thread_id=thread_id, amount=fetch_total)
            page = all_messages[offset : offset + amount]
            has_more = len(all_messages) >= fetch_total

            result_messages = []
            for m in page:
                msg_dict: dict[str, Any] = {
                    "sender": m.sender.username,
                    "text": m.content.text,
                    "media_type": m.content.media_type.value,
                    "timestamp": m.timestamp.isoformat(),
                }
                if m.is_sent_by_viewer:
                    msg_dict["seen_since"] = m.seen_since
                if m.content.media_url is not None:
                    msg_dict["media_url"] = m.content.media_url
                result_messages.append(msg_dict)

            return {
                "thread_id": thread_id,
                "messages": result_messages,
                "count": len(page),
                "offset": offset,
                "has_more": has_more,
            }
        except Exception as e:
            logger.exception("Error getting messages for thread %s", thread_id)
            return {"error": str(e), "thread_id": thread_id}

    @mcp.tool()
    def get_chat_log(thread_id: str, amount: int = 50, offset: int = 0) -> dict[str, Any]:
        """Get conversation as a readable chat log. Optimized for LLM analysis.

        Returns messages as a plain-text chronological log, ~5x smaller than JSON.
        Use this for conversation analysis, reconnaissance, or context loading.
        Use get_messages for structured data when you need individual fields.

        Args:
            thread_id: ID of the thread to get messages from.
            amount: Maximum number of messages to fetch (default: 50).
            offset: Skip the N most recent messages (default: 0).
                Use for pagination: offset=0 gets latest, offset=150 gets older.

        Returns:
            Object with 'thread_id', 'thread_title', 'log' (plain text),
            'count', 'offset', and 'has_more'.
        """
        try:
            fetch_total = offset + amount
            all_messages = client.get_messages(thread_id=thread_id, amount=fetch_total)
            page = all_messages[offset : offset + amount]
            has_more = len(all_messages) >= fetch_total

            # Reverse to chronological order (oldest first)
            chronological = list(reversed(page))

            # Find the last viewer message for seen_since annotation
            last_viewer_seen_since = None
            for m in page:  # page is newest-first
                if m.is_sent_by_viewer and m.seen_since is not None:
                    last_viewer_seen_since = m.seen_since
                    break

            lines: list[str] = []
            for m in chronological:
                # Skip action_log noise
                if m.content.media_type == MediaType.ACTION_LOG:
                    continue

                sender = "YOU" if m.is_sent_by_viewer else m.sender.username
                ts = m.timestamp.strftime("%b %d %H:%M")

                text = m.content.text or f"[{m.content.media_type.value}]"

                # Annotate seen_since on the chronologically last viewer message
                seen_tag = ""
                if (
                    m.is_sent_by_viewer
                    and m == chronological[-1]
                    and last_viewer_seen_since is not None
                ):
                    if last_viewer_seen_since < 60:
                        seen_tag = f" (seen {last_viewer_seen_since}m ago)"
                    else:
                        hours = last_viewer_seen_since // 60
                        seen_tag = f" (seen {hours}h ago)"

                lines.append(f"[{ts}] {sender}: {text}{seen_tag}")

            log_text = "\n".join(lines)

            return {
                "thread_id": thread_id,
                "log": log_text,
                "count": len(
                    [m for m in chronological if m.content.media_type != MediaType.ACTION_LOG]
                ),
                "offset": offset,
                "has_more": has_more,
            }
        except Exception as e:
            logger.exception("Error getting chat log for thread %s", thread_id)
            return {"error": str(e), "thread_id": thread_id}

    @mcp.tool(annotations={"destructive": True})
    def delete_message(thread_id: str, message_id: str) -> dict[str, Any]:
        """Delete a message from a thread.

        Args:
            thread_id: ID of the thread containing the message.
            message_id: ID of the message to delete.

        Returns:
            Object with 'success' boolean, 'thread_id', and 'message_id'.
        """
        try:
            success = client.delete_message(thread_id=thread_id, message_id=message_id)
            return {
                "success": success,
                "thread_id": thread_id,
                "message_id": message_id,
            }
        except Exception as e:
            logger.exception("Error deleting message %s from thread %s", message_id, thread_id)
            return {"error": str(e), "thread_id": thread_id, "message_id": message_id}

    @mcp.tool()
    def wait_for_reply(
        thread_id: str,
        timeout_minutes: int = 5,
        poll_interval_seconds: int = 3,
        double_text_grace_period_seconds: int = 10,
    ) -> dict[str, Any]:
        """Wait for new messages in a thread. Blocks until reply arrives or timeout.

        This tool first SYNCS (ensures our latest message is visible), then polls
        for new messages using TIMESTAMP-based detection to avoid race conditions.

        Use variable timeout_minutes based on your strategy:
        - 5 minutes: Quick checkpoint, good for active conversations
        - 30-60 minutes: Medium wait after soft resistance
        - 120-180 minutes: Longer cooldown after backing off
        - Longer: Waiting until morning, respecting their schedule

        Args:
            thread_id: ID of the thread to monitor.
            timeout_minutes: How long to wait before returning (default: 5).
                Use shorter times for active convos, longer for cooldowns.
            poll_interval_seconds: How often to check for new messages (default: 3).
            double_text_grace_period_seconds: Extra wait time after first reply
                to catch rapid follow-up messages (default: 10).

        Returns:
            On reply: Object with 'success', 'new_messages' array, 'waited_seconds'.
            On timeout: Object with 'timeout', 'waited_minutes'.
        """
        try:
            # MQTT FAST PATH: zero REST polling, <100ms latency
            mqtt = _get_mqtt_manager()
            if mqtt and mqtt.is_connected:
                self_uid = str(client.client.user_id) if client.client.user_id else ""
                umap = _get_user_map(client, thread_id)
                return _wait_for_reply_mqtt(
                    mqtt,
                    thread_id,
                    timeout_minutes,
                    double_text_grace_period_seconds,
                    self_user_id=self_uid,
                    user_map=umap,
                )

            # REST SLOW PATH: polling fallback
            return _wait_for_reply_rest(
                client,
                thread_id,
                timeout_minutes,
                poll_interval_seconds,
                double_text_grace_period_seconds,
            )
        except Exception as e:
            logger.exception("Error waiting for reply in thread %s", thread_id)
            return {"error": str(e), "thread_id": thread_id}
