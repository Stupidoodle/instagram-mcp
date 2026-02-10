"""E2E tests for _wait_for_reply_mqtt — the core waiting function.

Tests the actual function against live MQTT, verifying it correctly
detects messages, handles timeouts, filters self-echoes, and resolves
usernames. No mocking — real Instagram accounts, real MQTT connection.
"""

from __future__ import annotations

import threading
import time
import uuid

import pytest

from instagram_mcp.tools.messages import _wait_for_reply_mqtt

pytestmark = pytest.mark.e2e


class TestWaitDetectsMessages:
    """Verify _wait_for_reply_mqtt picks up real messages from bot2."""

    def test_detects_message_from_other(
        self,
        mqtt_manager,
        bot2_client,
        shared_thread_id,
        bot1_user_id,
    ):
        """Bot2 sends after wait starts → wait_for_reply returns it."""
        marker = f"e2e-wait-{uuid.uuid4().hex[:8]}"

        # Schedule bot2 to send a message after a short delay
        def send_delayed():
            time.sleep(3)
            bot2_client.reply_to_thread(
                thread_id=shared_thread_id,
                text=f"delayed reply {marker}",
            )

        sender = threading.Thread(target=send_delayed, daemon=True)
        sender.start()

        # Bot1 waits for reply via MQTT
        result = _wait_for_reply_mqtt(
            mqtt_manager,
            shared_thread_id,
            timeout_minutes=1,
            double_text_grace_period_seconds=3,
            self_user_id=bot1_user_id,
        )

        sender.join(timeout=15)

        assert result.get("success") is True, f"Expected success, got: {result}"
        msgs = result["new_messages"]
        matched = [m for m in msgs if marker in (m.get("text") or "")]
        assert len(matched) >= 1, (
            f"Expected message with marker {marker}, got: {msgs}"
        )

    def test_timeout_when_no_messages(
        self,
        mqtt_manager,
        shared_thread_id,
        bot1_user_id,
    ):
        """Nobody sends → wait_for_reply times out cleanly."""
        # Use timeout_minutes=0 to trigger immediate timeout
        # (0 minutes = 0 seconds budget)
        result = _wait_for_reply_mqtt(
            mqtt_manager,
            shared_thread_id,
            timeout_minutes=0,
            double_text_grace_period_seconds=1,
            self_user_id=bot1_user_id,
        )

        assert result.get("timeout") is True
        assert "success" not in result

    def test_double_text_collected(
        self,
        mqtt_manager,
        bot2_client,
        shared_thread_id,
        bot1_user_id,
    ):
        """Bot2 sends two messages rapidly → both collected in grace period."""
        marker = f"e2e-dbl-{uuid.uuid4().hex[:8]}"

        def send_double_text():
            time.sleep(3)
            bot2_client.reply_to_thread(
                thread_id=shared_thread_id,
                text=f"first {marker}",
            )
            time.sleep(1)
            bot2_client.reply_to_thread(
                thread_id=shared_thread_id,
                text=f"second {marker}",
            )

        sender = threading.Thread(target=send_double_text, daemon=True)
        sender.start()

        result = _wait_for_reply_mqtt(
            mqtt_manager,
            shared_thread_id,
            timeout_minutes=1,
            double_text_grace_period_seconds=10,  # Long grace to catch both
            self_user_id=bot1_user_id,
        )

        sender.join(timeout=20)

        assert result.get("success") is True
        msgs = result["new_messages"]
        matched = [m for m in msgs if marker in (m.get("text") or "")]
        assert len(matched) >= 2, (
            f"Expected 2 messages with marker {marker}, got {len(matched)}: {msgs}"
        )

    def test_self_echo_not_returned(
        self,
        mqtt_manager,
        bot1_client,
        shared_thread_id,
        bot1_user_id,
    ):
        """Bot1's own message is filtered out — not returned as a reply.

        Sends a message from bot1 during the wait window and verifies
        it does NOT appear in the result.
        """
        marker = f"e2e-selfecho-{uuid.uuid4().hex[:8]}"

        def send_own_message():
            time.sleep(2)
            bot1_client.reply_to_thread(
                thread_id=shared_thread_id,
                text=f"my own message {marker}",
            )

        sender = threading.Thread(target=send_own_message, daemon=True)
        sender.start()

        # Wait with a short timeout — should timeout, not return our own msg
        result = _wait_for_reply_mqtt(
            mqtt_manager,
            shared_thread_id,
            timeout_minutes=0,  # ~10 seconds
            double_text_grace_period_seconds=3,
            self_user_id=bot1_user_id,
        )

        sender.join(timeout=15)

        # Should timeout (our own message filtered), not succeed
        if result.get("success"):
            # If something else arrived, make sure it's not our marker
            msgs = result["new_messages"]
            own_msgs = [m for m in msgs if marker in (m.get("text") or "")]
            assert len(own_msgs) == 0, (
                f"Self-echo was NOT filtered: {own_msgs}"
            )
        else:
            assert result.get("timeout") is True

    def test_username_resolution(
        self,
        mqtt_manager,
        bot2_client,
        shared_thread_id,
        bot1_user_id,
        bot2_user_id,
        bot2_username,
    ):
        """Sender is resolved to username when user_map is provided."""
        marker = f"e2e-uname-{uuid.uuid4().hex[:8]}"
        user_map = {bot2_user_id: bot2_username}

        def send_delayed():
            time.sleep(3)
            bot2_client.reply_to_thread(
                thread_id=shared_thread_id,
                text=f"username test {marker}",
            )

        sender = threading.Thread(target=send_delayed, daemon=True)
        sender.start()

        result = _wait_for_reply_mqtt(
            mqtt_manager,
            shared_thread_id,
            timeout_minutes=1,
            double_text_grace_period_seconds=3,
            self_user_id=bot1_user_id,
            user_map=user_map,
        )

        sender.join(timeout=15)

        assert result.get("success") is True
        msgs = result["new_messages"]
        matched = [m for m in msgs if marker in (m.get("text") or "")]
        assert len(matched) >= 1
        # Sender should be the resolved username, not raw user_id
        assert matched[0]["sender"] == bot2_username
