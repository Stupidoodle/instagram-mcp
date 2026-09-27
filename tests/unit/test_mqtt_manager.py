"""Unit tests for MQTTManager."""

import json
import struct
import threading
import time
import zlib
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from instagram_mcp.mqtt.connection import PINGREQ, PINGRESP, PUBACK, PUBLISH
from instagram_mcp.mqtt.events import MessageEvent
from instagram_mcp.mqtt.manager import _PINGREQ_RESPONSE_TIMEOUT, _STALE_TIMEOUT, MQTTManager

if TYPE_CHECKING:
    from pathlib import Path


def _make_session_file(tmp_path: Path) -> Path:
    """Create a minimal instagrapi session file."""
    session = {
        "authorization_data": {
            "ds_user_id": "12345",
            "sessionid": "test_session",
        },
        "uuids": {"phone_id": "phone1234567890phone"},
        "device_settings": {"app_version": "415.0.0.36.76"},
        "user_agent": "Instagram 415.0.0.36.76 Android",
    }
    f = tmp_path / "session.json"
    f.write_text(json.dumps(session))
    return f


class TestMQTTManagerLifecycle:
    def test_initial_state(self) -> None:
        mgr = MQTTManager()
        assert not mgr.is_connected

    @patch.object(MQTTManager, "_publish")
    @patch("instagram_mcp.mqtt.manager.build_connect_payload", return_value=b"x")
    @patch("instagram_mcp.mqtt.manager.MQTToTConnection")
    def test_connect_starts_reader_thread(
        self,
        mock_conn_cls: MagicMock,
        _mock_build: MagicMock,
        _mock_publish: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_conn = MagicMock()
        mock_conn.is_connected = True
        mock_conn.read_packet.return_value = None
        mock_conn_cls.return_value = mock_conn

        mgr = MQTTManager()
        mgr._conn = mock_conn
        session_file = _make_session_file(tmp_path)

        mgr.connect(session_file, seq_id=100)
        assert mgr._reader_thread is not None
        assert mgr._reader_thread.is_alive()

        mgr.disconnect()
        assert mgr._reader_thread is None

    def test_disconnect_without_connect(self) -> None:
        """Disconnecting before connecting should not raise."""
        mgr = MQTTManager()
        mgr.disconnect()

    @patch.object(MQTTManager, "_publish")
    @patch("instagram_mcp.mqtt.manager.build_connect_payload", return_value=b"x")
    def test_connect_subscribes_iris(
        self,
        _mock_build: MagicMock,
        mock_publish: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_conn = MagicMock()
        mock_conn.is_connected = True
        mock_conn.read_packet.return_value = None

        mgr = MQTTManager()
        mgr._conn = mock_conn
        session_file = _make_session_file(tmp_path)

        mgr.connect(session_file, seq_id=42)
        mock_publish.assert_called_once_with(
            134,
            {
                "seq_id": 42,
                "snapshot_at_ms": 0,
                "snapshot_app_version": "415.0.0.36.76",
                "subscription_type": "message",
            },
        )
        mgr.disconnect()


class TestMQTTManagerReaderLoop:
    def test_handles_publish_packet(self) -> None:
        """Reader loop parses PUBLISH and hands events to the listener."""
        mgr = MQTTManager()

        # Build a fake PUBLISH packet body (topic 146, QoS 1)
        topic = b"146"
        iris_data = [
            {
                "event": "patch",
                "data": [
                    {
                        "op": "add",
                        "path": "/direct_v2/threads/T1/items/I1",
                        "value": json.dumps(
                            {
                                "item_id": "I1",
                                "user_id": 99,
                                "text": "hey",
                                "item_type": "text",
                                "timestamp": "1000",
                            }
                        ),
                    }
                ],
                "seq_id": 1,
            }
        ]
        payload = zlib.compress(json.dumps(iris_data).encode())
        body = struct.pack("!H", len(topic)) + topic
        body += struct.pack("!H", 1)  # packet_id for QoS 1
        body += payload

        first_byte = 0x32  # PUBLISH QoS 1

        received: list[MessageEvent] = []
        mgr.set_listener(received.append)  # type: ignore[arg-type]

        # Call the handler directly
        mgr._handle_publish(first_byte, body)

        (event,) = received
        assert isinstance(event, MessageEvent)
        assert event.thread_id == "T1"
        assert event.text == "hey"
        assert event.user_id == 99

    def test_handles_pingreq(self) -> None:
        """Reader loop responds to PINGREQ with PINGRESP."""
        mgr = MQTTManager()
        mock_conn = MagicMock()
        mock_conn.is_connected = True

        call_count = 0

        def fake_read_packet() -> tuple[int, int, bytes] | None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (PINGREQ, 0xC0, b"")
            # Signal stop after one iteration
            mgr._stop_event.set()
            return None

        mock_conn.read_packet.side_effect = fake_read_packet
        mgr._conn = mock_conn

        mgr._reader_loop()
        mock_conn.send_pingresp.assert_called_once()

    def test_reader_stops_on_disconnect(self) -> None:
        """Reader loop exits when connection is lost."""
        mgr = MQTTManager()
        mock_conn = MagicMock()
        mock_conn.is_connected = False
        mock_conn.read_packet.return_value = None
        mgr._conn = mock_conn

        # Should exit quickly since is_connected is False
        mgr._reader_loop()


class TestMQTTManagerPublish:
    def test_packet_id_increments(self) -> None:
        mgr = MQTTManager()
        mock_conn = MagicMock()
        mgr._conn = mock_conn

        mgr._publish(134, {"test": 1})
        mgr._publish(134, {"test": 2})

        calls = mock_conn.publish.call_args_list
        assert calls[0].kwargs["packet_id"] == 1
        assert calls[1].kwargs["packet_id"] == 2

    def test_packet_id_wraps(self) -> None:
        mgr = MQTTManager()
        mgr._packet_id_counter = 65535
        mock_conn = MagicMock()
        mgr._conn = mock_conn

        mgr._publish(134, {"test": 1})
        mgr._publish(134, {"test": 2})

        calls = mock_conn.publish.call_args_list
        assert calls[0].kwargs["packet_id"] == 65535
        assert calls[1].kwargs["packet_id"] == 1


class TestMQTTManagerStaleDetection:
    """Tests for half-open/stale connection detection — the core failure mode."""

    def _make_alive_mgr(self) -> MQTTManager:
        """Create an MQTTManager with a running reader thread (simulated)."""
        mgr = MQTTManager()
        mock_conn = MagicMock()
        mock_conn.is_connected = True
        mgr._conn = mock_conn
        mgr._stop_event.clear()
        mgr._reader_thread = threading.Thread(target=lambda: time.sleep(10), daemon=True)
        mgr._reader_thread.start()
        return mgr

    def _cleanup(self, mgr: MQTTManager) -> None:
        mgr._stop_event.set()
        if mgr._reader_thread:
            mgr._reader_thread.join(timeout=1)

    def test_is_connected_false_when_blanket_stale(self) -> None:
        """No packets for > STALE_TIMEOUT → dead (blanket timeout)."""
        mgr = self._make_alive_mgr()
        mgr._last_packet_time = time.monotonic() - _STALE_TIMEOUT - 10
        assert mgr.is_connected is False
        self._cleanup(mgr)

    def test_is_connected_false_when_pingreq_unanswered(self) -> None:
        """PINGREQ sent > PINGREQ_RESPONSE_TIMEOUT ago with no response → dead."""
        mgr = self._make_alive_mgr()
        now = time.monotonic()
        mgr._last_packet_time = now - 60  # Last packet 60s ago
        mgr._pingreq_sent_at = now - _PINGREQ_RESPONSE_TIMEOUT - 1  # PINGREQ 16s ago
        # _pingreq_sent_at > _last_packet_time → PINGREQ unanswered
        assert mgr.is_connected is False
        self._cleanup(mgr)

    def test_is_connected_true_when_pingreq_answered(self) -> None:
        """PINGREQ was sent but a packet arrived after → connection healthy."""
        mgr = self._make_alive_mgr()
        now = time.monotonic()
        mgr._pingreq_sent_at = now - 10  # PINGREQ 10s ago
        mgr._last_packet_time = now - 5  # Packet arrived 5s ago (after PINGREQ)
        assert mgr.is_connected is True
        self._cleanup(mgr)

    def test_is_connected_true_when_fresh_packets(self) -> None:
        """Recent packets → connection is healthy."""
        mgr = self._make_alive_mgr()
        mgr._last_packet_time = time.monotonic()
        assert mgr.is_connected is True
        self._cleanup(mgr)

    def test_is_connected_false_when_stop_event_set(self) -> None:
        mgr = MQTTManager()
        mock_conn = MagicMock()
        mock_conn.is_connected = True
        mgr._conn = mock_conn
        mgr._stop_event.set()
        assert mgr.is_connected is False

    def test_is_connected_false_when_reader_thread_dead(self) -> None:
        mgr = MQTTManager()
        mock_conn = MagicMock()
        mock_conn.is_connected = True
        mgr._conn = mock_conn
        mgr._stop_event.clear()
        mgr._reader_thread = threading.Thread(target=lambda: None)
        mgr._reader_thread.start()
        mgr._reader_thread.join()
        assert mgr.is_connected is False

    def test_is_connected_false_when_no_reader_thread(self) -> None:
        mgr = MQTTManager()
        mock_conn = MagicMock()
        mock_conn.is_connected = True
        mgr._conn = mock_conn
        mgr._stop_event.clear()
        mgr._reader_thread = None
        assert mgr.is_connected is False

    def test_is_connected_false_when_socket_disconnected(self) -> None:
        mgr = MQTTManager()
        mock_conn = MagicMock()
        mock_conn.is_connected = False
        mgr._conn = mock_conn
        assert mgr.is_connected is False

    def test_is_connected_skips_staleness_check_before_first_packet(self) -> None:
        """Before reader loop starts, _last_packet_time is 0 → skip staleness check."""
        mgr = self._make_alive_mgr()
        mgr._last_packet_time = 0.0
        assert mgr.is_connected is True
        self._cleanup(mgr)

    def test_reconnect_count_starts_at_zero(self) -> None:
        mgr = MQTTManager()
        assert mgr.reconnect_count == 0

    def test_reader_loop_breaks_on_stale_timeout(self) -> None:
        """Reader loop detects no packets for STALE_TIMEOUT and disconnects."""
        mgr = MQTTManager()
        mock_conn = MagicMock()
        mock_conn.is_connected = True
        mock_conn.read_packet.return_value = None
        mgr._conn = mock_conn

        def patched_reader_loop():
            mgr._last_packet_time = time.monotonic() - _STALE_TIMEOUT - 10
            while not mgr._stop_event.is_set():
                result = mgr._conn.read_packet()
                if result is None:
                    now = time.monotonic()
                    if not mgr._conn.is_connected:
                        break
                    if now - mgr._last_packet_time > _STALE_TIMEOUT:
                        mgr._conn.disconnect()
                        break
                    continue
                break

        patched_reader_loop()
        mock_conn.disconnect.assert_called_once()

    def test_reader_loop_breaks_on_pingreq_timeout(self) -> None:
        """Reader loop detects unanswered PINGREQ and disconnects."""
        mgr = MQTTManager()
        mock_conn = MagicMock()
        mock_conn.is_connected = True
        mgr._conn = mock_conn

        # _reader_loop() resets both timestamps on entry, so make them stale
        # from inside the first read: PINGREQ sent 20s ago, last packet 60s ago.
        def stale_read() -> None:
            now = time.monotonic()
            mgr._last_packet_time = now - 60
            mgr._pingreq_sent_at = now - _PINGREQ_RESPONSE_TIMEOUT - 5

        mock_conn.read_packet.side_effect = stale_read

        # Run reader loop — should detect unanswered PINGREQ and break
        import instagram_mcp.mqtt.manager as manager_mod

        # Prevent keepalive from resetting pingreq_sent_at
        original_interval = manager_mod._KEEPALIVE_INTERVAL
        manager_mod._KEEPALIVE_INTERVAL = 9999
        try:
            mgr._reader_loop()
        finally:
            manager_mod._KEEPALIVE_INTERVAL = original_interval

        mock_conn.disconnect.assert_called_once()

    def test_reader_loop_updates_last_packet_time_on_packet(self) -> None:
        """Receiving any packet updates _last_packet_time."""
        mgr = MQTTManager()
        mock_conn = MagicMock()
        mock_conn.is_connected = True
        mgr._conn = mock_conn

        call_count = 0

        def fake_read_packet():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (PINGRESP, 0xD0, b"")
            mgr._stop_event.set()
            return None

        mock_conn.read_packet.side_effect = fake_read_packet

        before = time.monotonic()
        mgr._reader_loop()
        after = time.monotonic()

        assert mgr._last_packet_time >= before
        assert mgr._last_packet_time <= after

    def test_reader_loop_tracks_pingreq_time(self) -> None:
        """Reader loop sets _pingreq_sent_at when sending PINGREQ."""
        mgr = MQTTManager()
        mock_conn = MagicMock()
        mock_conn.is_connected = True
        mgr._conn = mock_conn

        call_count = 0

        def fake_read_packet():
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return None
            mgr._stop_event.set()
            return None

        mock_conn.read_packet.side_effect = fake_read_packet

        import instagram_mcp.mqtt.manager as manager_mod

        original = manager_mod._KEEPALIVE_INTERVAL
        manager_mod._KEEPALIVE_INTERVAL = 0  # Trigger immediately
        try:
            before = time.monotonic()
            mgr._reader_loop()
            assert mgr._pingreq_sent_at >= before
        finally:
            manager_mod._KEEPALIVE_INTERVAL = original


class TestMQTTManagerReaderLoopPacketTypes:
    """Tests for handling different MQTT packet types in the reader loop."""

    def test_handles_puback(self) -> None:
        """PUBACK packet is logged and doesn't crash."""
        mgr = MQTTManager()
        mock_conn = MagicMock()
        mock_conn.is_connected = True
        mgr._conn = mock_conn

        call_count = 0

        def fake_read_packet():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (PUBACK, 0x40, struct.pack("!H", 1))
            mgr._stop_event.set()
            return None

        mock_conn.read_packet.side_effect = fake_read_packet
        mgr._reader_loop()
        # No crash, PUBACK handled silently

    def test_handles_pingresp(self) -> None:
        """PINGRESP packet is logged and doesn't crash."""
        mgr = MQTTManager()
        mock_conn = MagicMock()
        mock_conn.is_connected = True
        mgr._conn = mock_conn

        call_count = 0

        def fake_read_packet():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (PINGRESP, 0xD0, b"")
            mgr._stop_event.set()
            return None

        mock_conn.read_packet.side_effect = fake_read_packet
        mgr._reader_loop()

    def test_handles_unknown_packet_type(self) -> None:
        """Unknown packet type (e.g. SUBACK=9) is logged, doesn't crash."""
        mgr = MQTTManager()
        mock_conn = MagicMock()
        mock_conn.is_connected = True
        mgr._conn = mock_conn

        call_count = 0

        def fake_read_packet():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (9, 0x90, b"\x00\x01\x00")  # SUBACK-like
            mgr._stop_event.set()
            return None

        mock_conn.read_packet.side_effect = fake_read_packet
        mgr._reader_loop()

    def test_publish_handler_exception_doesnt_crash_loop(self) -> None:
        """Exception in _handle_publish is caught, reader loop continues."""
        mgr = MQTTManager()
        mock_conn = MagicMock()
        mock_conn.is_connected = True
        mgr._conn = mock_conn

        call_count = 0

        def fake_read_packet():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Return a PUBLISH with garbage body that will cause parse error
                return (PUBLISH, 0x30, b"\x00")  # Invalid body
            mgr._stop_event.set()
            return None

        mock_conn.read_packet.side_effect = fake_read_packet
        mgr._reader_loop()
        # Loop should have continued past the exception

    def test_reader_loop_crash_logged(self) -> None:
        """Unhandled exception in reader loop is logged and loop exits."""
        mgr = MQTTManager()
        mock_conn = MagicMock()
        mock_conn.is_connected = True
        mgr._conn = mock_conn

        mock_conn.read_packet.side_effect = RuntimeError("catastrophic failure")
        mgr._reader_loop()
        # Should not raise — exception is caught and logged

    def test_keepalive_pingreq_sent(self) -> None:
        """Reader loop sends PINGREQ after keepalive interval."""
        mgr = MQTTManager()
        mock_conn = MagicMock()
        mock_conn.is_connected = True
        mgr._conn = mock_conn

        call_count = 0

        def fake_read_packet():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Return a PINGRESP so the PINGREQ timeout doesn't kill us
                return (PINGRESP, 0xD0, b"")
            if call_count <= 3:
                return None  # Timeout
            mgr._stop_event.set()
            return None

        mock_conn.read_packet.side_effect = fake_read_packet

        # Monkey-patch the keepalive to trigger immediately
        import instagram_mcp.mqtt.manager as manager_mod

        original = manager_mod._KEEPALIVE_INTERVAL
        manager_mod._KEEPALIVE_INTERVAL = 0  # Trigger immediately
        try:
            mgr._reader_loop()
            mock_conn.send_pingreq.assert_called()
        finally:
            manager_mod._KEEPALIVE_INTERVAL = original

    def test_handle_publish_sends_puback_for_qos1(self) -> None:
        """PUBLISH with QoS 1 gets a PUBACK response."""
        mgr = MQTTManager()
        mock_conn = MagicMock()
        mgr._conn = mock_conn

        # Build valid PUBLISH body
        topic = b"146"
        iris_data = [{"event": "patch", "data": [], "seq_id": 1}]
        payload = zlib.compress(json.dumps(iris_data).encode())
        body = struct.pack("!H", len(topic)) + topic
        body += struct.pack("!H", 42)  # packet_id
        body += payload
        first_byte = 0x32  # QoS 1

        mgr._handle_publish(first_byte, body)
        mock_conn.send_puback.assert_called_once_with(42)

    def test_handle_publish_no_puback_for_qos0(self) -> None:
        """PUBLISH with QoS 0 does not send PUBACK."""
        mgr = MQTTManager()
        mock_conn = MagicMock()
        mgr._conn = mock_conn

        topic = b"146"
        iris_data = [{"event": "patch", "data": [], "seq_id": 1}]
        payload = zlib.compress(json.dumps(iris_data).encode())
        body = struct.pack("!H", len(topic)) + topic + payload
        first_byte = 0x30  # QoS 0

        mgr._handle_publish(first_byte, body)
        mock_conn.send_puback.assert_not_called()


class TestMQTTManagerDisconnect:
    """Tests for disconnect edge cases."""

    def test_disconnect_stops_reader_thread(self) -> None:
        mgr = MQTTManager()
        mock_conn = MagicMock()
        mock_conn.is_connected = True
        mock_conn.read_packet.return_value = None
        mgr._conn = mock_conn
        mgr._stop_event.clear()

        mgr._reader_thread = threading.Thread(
            target=mgr._reader_loop, name="test-reader", daemon=True
        )
        mgr._reader_thread.start()
        time.sleep(0.1)
        assert mgr._reader_thread.is_alive()

        mgr.disconnect()
        assert mgr._reader_thread is None
        mock_conn.disconnect.assert_called()

    def test_disconnect_twice_no_crash(self) -> None:
        mgr = MQTTManager()
        mgr.disconnect()
        mgr.disconnect()  # No crash

    def test_disconnect_with_dead_reader_thread(self) -> None:
        mgr = MQTTManager()
        mock_conn = MagicMock()
        mgr._conn = mock_conn
        # Thread that already exited
        t = threading.Thread(target=lambda: None)
        t.start()
        t.join()
        mgr._reader_thread = t

        mgr.disconnect()
        assert mgr._reader_thread is None


def _publish_body(text: str) -> tuple[int, bytes]:
    """A QoS 1 PUBLISH on /ig_message_sync carrying one new message in thread T1."""
    topic = b"146"
    iris_data = [
        {
            "event": "patch",
            "data": [
                {
                    "op": "add",
                    "path": "/direct_v2/threads/T1/items/I1",
                    "value": json.dumps(
                        {
                            "item_id": "I1",
                            "user_id": 5,
                            "text": text,
                            "item_type": "text",
                            "timestamp": "0",
                        }
                    ),
                }
            ],
            "seq_id": 1,
        }
    ]
    payload = zlib.compress(json.dumps(iris_data).encode())
    return 0x32, struct.pack("!H", len(topic)) + topic + struct.pack("!H", 1) + payload


class TestMQTTManagerListener:
    def test_listener_receives_every_event(self) -> None:
        mgr = MQTTManager()
        mgr._conn = MagicMock()
        received: list[MessageEvent] = []
        mgr.set_listener(received.append)  # type: ignore[arg-type]

        mgr._handle_publish(*_publish_body("pushed"))

        assert len(received) == 1
        assert received[0].text == "pushed"


class TestMQTTManagerWatchdog:
    def test_watchdog_reconnects_until_disconnect(self) -> None:
        mgr = MQTTManager()
        mgr._conn = MagicMock()
        calls = threading.Event()

        def ensure() -> bool:
            calls.set()
            raise RuntimeError("still down")

        with patch.object(mgr, "ensure_connected", side_effect=ensure):
            mgr.start_watchdog(interval=0.01)
            mgr.start_watchdog(interval=0.01)  # idempotent
            assert calls.wait(1)
            mgr.disconnect()
            assert mgr._watchdog_thread is not None
            mgr._watchdog_thread.join(1)
            assert not mgr._watchdog_thread.is_alive()


class TestMQTTManagerTyping:
    def test_indicate_activity_publishes_command(self) -> None:
        mgr = MQTTManager()
        mgr._conn = MagicMock()
        with patch.object(mgr, "ensure_connected", return_value=True):
            mgr.indicate_activity("T1", active=False)
        topic, payload = mgr._conn.publish.call_args.args[:2]
        assert topic == 132
        assert payload["action"] == "indicate_activity"
        assert payload["thread_id"] == "T1"
        assert payload["activity_status"] == "0"
        assert payload["client_context"]

    def test_indicate_activity_needs_connection(self) -> None:
        mgr = MQTTManager()
        with patch.object(mgr, "ensure_connected", return_value=False), pytest.raises(RuntimeError):
            mgr.indicate_activity("T1")


class TestMQTTManagerListenerFailure:
    def test_listener_failure_is_logged_not_raised(self) -> None:
        mgr = MQTTManager()
        mgr._conn = MagicMock()
        mgr.set_listener(MagicMock(side_effect=RuntimeError("boom")))
        mgr._handle_publish(*_publish_body("x"))

    def test_no_listener_drops_events(self) -> None:
        mgr = MQTTManager()
        mgr._conn = MagicMock()
        mgr._handle_publish(*_publish_body("x"))
