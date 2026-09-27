"""E2E tests for MQTTManager methods (is_connected, reconnect, stale detection).

These test the manager's actual behavior with a live MQTT connection,
covering the unhappy paths that unit tests mock away.
"""

from __future__ import annotations

import time

import pytest

from instagram_mcp.mqtt.manager import _STALE_TIMEOUT, MQTTManager

pytestmark = pytest.mark.e2e


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
