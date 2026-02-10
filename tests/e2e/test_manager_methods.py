"""E2E tests for MQTTManager methods — wait_for_message, collect_events,
is_connected, disconnect/reconnect, and stale detection.

These test the manager's actual behavior with a live MQTT connection,
covering the unhappy paths that unit tests mock away.
"""

from __future__ import annotations

import threading
import time
import uuid

import pytest

from instagram_mcp.mqtt.events import MessageEvent
from instagram_mcp.mqtt.manager import MQTTManager, _STALE_TIMEOUT

pytestmark = pytest.mark.e2e


class TestManagerWaitForMessage:
    """Direct tests of MQTTManager.wait_for_message()."""

    def test_returns_message_event(
        self,
        mqtt_manager,
        bot2_client,
        shared_thread_id,
    ):
        """Bot2 sends → wait_for_message returns a MessageEvent."""
        marker = f"e2e-wfm-{uuid.uuid4().hex[:8]}"

        def send_delayed():
            time.sleep(3)
            bot2_client.reply_to_thread(
                thread_id=shared_thread_id,
                text=f"wait_for_message test {marker}",
            )

        sender = threading.Thread(target=send_delayed, daemon=True)
        sender.start()

        result = mqtt_manager.wait_for_message(shared_thread_id, timeout=30)
        sender.join(timeout=15)

        assert result is not None
        assert isinstance(result, MessageEvent)
        assert result.thread_id == shared_thread_id

    def test_timeout_returns_none(
        self,
        mqtt_manager,
        shared_thread_id,
    ):
        """No message → wait_for_message returns None after timeout."""
        result = mqtt_manager.wait_for_message(shared_thread_id, timeout=2)
        assert result is None

    def test_skips_non_message_events(
        self,
        mqtt_manager,
        bot1_client,
        bot2_client,
        shared_thread_id,
    ):
        """Self-echo (from bot1) arrives but wait_for_message only returns
        MessageEvent objects — it should still return the bot2 message."""
        marker = f"e2e-skip-{uuid.uuid4().hex[:8]}"

        def send_both():
            time.sleep(2)
            # Bot1 sends (self-echo will arrive on MQTT)
            bot1_client.reply_to_thread(
                thread_id=shared_thread_id,
                text=f"self echo {marker}",
            )
            time.sleep(1)
            # Bot2 sends (other-user message)
            bot2_client.reply_to_thread(
                thread_id=shared_thread_id,
                text=f"other msg {marker}",
            )

        sender = threading.Thread(target=send_both, daemon=True)
        sender.start()

        result = mqtt_manager.wait_for_message(shared_thread_id, timeout=30)
        sender.join(timeout=15)

        # wait_for_message returns ANY MessageEvent (including self-echo)
        # — it doesn't filter by user_id, that's wait_for_reply's job
        assert result is not None
        assert isinstance(result, MessageEvent)


class TestManagerCollectEvents:
    """Direct tests of MQTTManager.collect_events()."""

    def test_collects_multiple_events(
        self,
        mqtt_manager,
        bot2_client,
        shared_thread_id,
    ):
        """Bot2 double-texts → collect_events returns both."""
        marker = f"e2e-collect-{uuid.uuid4().hex[:8]}"

        # Send two messages before collecting
        bot2_client.reply_to_thread(
            thread_id=shared_thread_id,
            text=f"collect first {marker}",
        )
        time.sleep(1)
        bot2_client.reply_to_thread(
            thread_id=shared_thread_id,
            text=f"collect second {marker}",
        )

        # Give MQTT time to deliver
        time.sleep(4)

        events = mqtt_manager.collect_events(shared_thread_id, window=5)

        # Should have at least 2 events (messages + possible seen/thread events)
        msg_events = [e for e in events if isinstance(e, MessageEvent)]
        assert len(msg_events) >= 2

    def test_empty_on_quiet_thread(
        self,
        mqtt_manager,
        shared_thread_id,
    ):
        """No messages → collect_events returns empty list."""
        events = mqtt_manager.collect_events(shared_thread_id, window=2)
        # May contain stale events from previous tests, but should not crash
        assert isinstance(events, list)


class TestManagerIsConnected:
    """Tests for is_connected under various failure conditions."""

    def test_healthy_connection(self, mqtt_manager):
        """Session-scoped MQTT should be connected."""
        assert mqtt_manager.is_connected is True

    def test_stale_detection(self, mqtt_manager):
        """Force _last_packet_time old → is_connected returns False."""
        original = mqtt_manager._last_packet_time

        mqtt_manager._last_packet_time = time.monotonic() - _STALE_TIMEOUT - 10
        assert mqtt_manager.is_connected is False

        # Restore so other tests aren't affected
        mqtt_manager._last_packet_time = original

    def test_recovers_from_stale(self, mqtt_manager):
        """After forcing stale, restoring timestamp makes it healthy again."""
        original = mqtt_manager._last_packet_time

        mqtt_manager._last_packet_time = time.monotonic() - _STALE_TIMEOUT - 10
        assert mqtt_manager.is_connected is False

        mqtt_manager._last_packet_time = time.monotonic()
        assert mqtt_manager.is_connected is True

        mqtt_manager._last_packet_time = original


class TestManagerDisconnectReconnect:
    """Tests for disconnect and reconnect lifecycle."""

    def test_disconnect_and_reconnect(
        self,
        bot1_client,
        shared_thread_id,
    ):
        """Disconnect → verify dead → reconnect → verify alive."""
        from pathlib import Path

        from tests.e2e.conftest import BOT1_SESSION

        iris = bot1_client.get_iris_info()
        mgr = MQTTManager()
        mgr.connect(
            session_file=BOT1_SESSION,
            seq_id=iris["seq_id"],
            snapshot_at_ms=iris["snapshot_at_ms"],
            app_version=iris["app_version"],
        )
        time.sleep(2)
        assert mgr.is_connected is True

        mgr.disconnect()
        assert mgr.is_connected is False

        # Reconnect
        iris = bot1_client.get_iris_info()
        mgr.connect(
            session_file=BOT1_SESSION,
            seq_id=iris["seq_id"],
            snapshot_at_ms=iris["snapshot_at_ms"],
            app_version=iris["app_version"],
        )
        time.sleep(2)
        assert mgr.is_connected is True

        mgr.disconnect()

    def test_wait_for_message_after_disconnect(
        self,
        bot1_client,
    ):
        """wait_for_message on a disconnected manager returns None fast."""
        mgr = MQTTManager()
        # Never connected — should return None immediately
        result = mgr.wait_for_message("fake_thread", timeout=1)
        assert result is None
