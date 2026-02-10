"""E2E tests for connection failure detection and auto-reconnect.

Test 1: Kill the connection → verify is_connected detects it's dead.
Test 2: Kill the connection → auto-reconnect → prove MQTT works again.
"""

from __future__ import annotations

import time
import uuid

import pytest

from instagram_mcp.mqtt.events import MessageEvent
from instagram_mcp.mqtt.manager import MQTTManager

pytestmark = pytest.mark.e2e


class TestConnectionKillDetection:
    """Kill the MQTT connection and verify we detect it."""

    def test_detect_killed_connection(self, bot1_client):
        """Forcibly close the socket → is_connected returns False."""
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

        # Sanity: connection is alive
        assert mgr.is_connected is True

        # Kill it — close the raw socket underneath
        mgr._conn._sock.close()
        mgr._conn._sock = None

        # Reader loop should notice within a few seconds
        time.sleep(3)

        assert mgr.is_connected is False

        mgr.disconnect()


class TestAutoReconnect:
    """Kill the connection, reconnect, prove MQTT delivers messages again."""

    def test_reconnect_after_kill(
        self,
        bot1_client,
        bot2_client,
        shared_thread_id,
    ):
        """Kill socket → ensure_connected() recovers → bot2 msg arrives."""
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

        # Kill the socket
        mgr._conn._sock.close()
        mgr._conn._sock = None
        time.sleep(3)
        assert mgr.is_connected is False

        # Auto-reconnect
        recovered = mgr.ensure_connected()
        assert recovered is True
        assert mgr.is_connected is True

        # Prove it works: bot2 sends a message, we receive it via MQTT
        marker = f"e2e-reconnect-{uuid.uuid4().hex[:8]}"

        q = mgr.router.subscribe(shared_thread_id)
        bot2_client.reply_to_thread(
            thread_id=shared_thread_id,
            text=f"after reconnect {marker}",
        )

        # Wait for the message to arrive
        event = None
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                evt = q.get(timeout=1)
                if isinstance(evt, MessageEvent) and marker in (evt.text or ""):
                    event = evt
                    break
            except Exception:
                pass

        mgr.router.unsubscribe(shared_thread_id, q)
        mgr.disconnect()

        assert event is not None, f"Message with marker {marker} never arrived after reconnect"
        assert marker in event.text
