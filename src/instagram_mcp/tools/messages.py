"""Message operation tools for Instagram MCP Server.

This module provides MCP tools for sending, receiving, and managing
Instagram Direct Messages.
"""

import logging
import time
from typing import TYPE_CHECKING, Any

from instagram_mcp.models.schemas import MediaType

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from instagram_mcp.client import InstagramClient

logger = logging.getLogger("instagram_mcp")


def register_message_tools(mcp: "FastMCP", client: "InstagramClient") -> None:
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
    def send_and_check(  # noqa: PLR0912
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
            message_id: ID of the sent message.
            has_interjection: True if they sent something since you started.
            interjection: Their message if has_interjection, else None.
            recent_messages: Last 5 messages for context.
        """
        try:
            # 1. Get baseline BEFORE sending (their latest message timestamp)
            pre_send_messages = client.get_messages(thread_id=thread_id, amount=5)
            baseline_timestamp = None
            for msg in pre_send_messages:
                if not msg.is_sent_by_viewer:
                    baseline_timestamp = msg.timestamp
                    break

            # 2. Send the message
            message = client.reply_to_thread(thread_id=thread_id, text=text)
            if not message:
                return {"success": False, "error": "Failed to send message"}

            sent_message_id = message.message_id
            logger.info("Sent message %s, syncing...", sent_message_id)

            # 3. Sync: poll until our message is visible
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

            # 4. Check for interjections (messages from them after baseline)
            post_send_messages = client.get_messages(thread_id=thread_id, amount=10)
            interjections = []
            for msg in post_send_messages:
                if msg.is_sent_by_viewer:
                    continue  # Skip our messages
                if baseline_timestamp and msg.timestamp <= baseline_timestamp:
                    continue  # Skip messages before our baseline
                interjections.append(msg)

            # Build recent messages for context (last 5, oldest first)
            recent = post_send_messages[:5]
            recent.reverse()

            # Build compact recent messages (last 5, oldest first)
            recent_list = []
            for m in recent:
                msg_dict: dict[str, Any] = {
                    "sender": m.sender.username,
                    "text": m.content.text,
                }
                if m.seen_since is not None:
                    msg_dict["seen_since"] = m.seen_since
                recent_list.append(msg_dict)

            result_dict: dict[str, Any] = {
                "success": True,
                "message_id": sent_message_id,
                "thread_id": thread_id,
                "text": text,
                "synced": synced,
                "has_interjection": len(interjections) > 0,
                "recent_messages": recent_list,
            }
            if interjections:
                result_dict["interjection"] = {
                    "sender": interjections[0].sender.username,
                    "text": interjections[0].content.text,
                    "timestamp": interjections[0].timestamp.isoformat(),
                }

            return result_dict

        except Exception as e:
            logger.exception("Error in send_and_check for thread %s", thread_id)
            return {"error": str(e), "thread_id": thread_id}

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
    def wait_for_reply(  # noqa: PLR0912
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
            # SYNC PHASE: Poll until our latest message is visible and get its timestamp
            # This ensures we're in sync with Instagram's state before waiting
            sync_start = time.time()
            baseline_timestamp = None
            sync_attempts = 0

            while time.time() - sync_start < 30:  # 30 sec sync timeout
                sync_attempts += 1
                current_messages = client.get_messages(thread_id=thread_id, amount=10)

                # Find OUR latest sent message's TIMESTAMP as baseline
                for msg in current_messages:
                    if msg.is_sent_by_viewer:
                        baseline_timestamp = msg.timestamp
                        break

                if baseline_timestamp:
                    break
                time.sleep(1)

            logger.info(
                "Waiting for reply in thread %s (baseline timestamp: %s, synced in %d attempts)",
                thread_id,
                baseline_timestamp,
                sync_attempts,
            )

            timeout_seconds = timeout_minutes * 60
            start_time = time.time()
            first_new_message_time = None

            while True:
                elapsed = time.time() - start_time

                # Check for timeout
                if elapsed >= timeout_seconds:
                    waited_minutes = int(elapsed / 60)
                    return {
                        "timeout": True,
                        "thread_id": thread_id,
                        "waited_minutes": waited_minutes,
                    }

                # Poll for new messages
                messages = client.get_messages(thread_id=thread_id, amount=10)

                # Find new messages using TIMESTAMP comparison
                # This catches all messages from them sent AFTER our baseline,
                # regardless of how Instagram orders the message list
                new_messages = []
                for msg in messages:
                    # Skip our own messages
                    if msg.is_sent_by_viewer:
                        continue
                    # Skip messages at or before our baseline timestamp
                    if baseline_timestamp and msg.timestamp <= baseline_timestamp:
                        continue
                    # Skip action_log messages (likes, reactions, thread updates)
                    # These are not actual replies we want to respond to
                    if msg.content.media_type == MediaType.ACTION_LOG:
                        continue
                    new_messages.append(msg)

                if new_messages:
                    # First new message detected
                    if first_new_message_time is None:
                        first_new_message_time = time.time()
                        logger.info(
                            "New message detected, waiting %ds for double texts...",
                            double_text_grace_period_seconds,
                        )

                    # Check if grace period has passed
                    grace_elapsed = time.time() - first_new_message_time
                    if grace_elapsed >= double_text_grace_period_seconds:
                        # Grace period done, return all new messages
                        # Sort by timestamp (oldest first) for natural reading order
                        new_messages.sort(key=lambda m: m.timestamp)

                        return {
                            "success": True,
                            "thread_id": thread_id,
                            "new_messages": [
                                {
                                    "sender": m.sender.username,
                                    "text": m.content.text,
                                    "media_type": m.content.media_type.value,
                                    "timestamp": m.timestamp.isoformat(),
                                }
                                for m in new_messages
                            ],
                            "message_count": len(new_messages),
                            "waited_seconds": int(elapsed),
                        }

                # Sleep before next poll
                time.sleep(poll_interval_seconds)

        except Exception as e:
            logger.exception("Error waiting for reply in thread %s", thread_id)
            return {"error": str(e), "thread_id": thread_id}
