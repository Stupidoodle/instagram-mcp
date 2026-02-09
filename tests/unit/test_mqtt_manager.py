"""Unit tests for MQTTManager."""

import json
import struct
import threading
import time
import zlib
from pathlib import Path
from unittest.mock import MagicMock, patch

from instagram_mcp.mqtt.connection import PINGREQ
from instagram_mcp.mqtt.events import MessageEvent
from instagram_mcp.mqtt.manager import MQTTManager


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
        """Reader loop parses PUBLISH and delivers events to router."""
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

        # Subscribe to thread T1
        q = mgr.router.subscribe("T1")

        # Call the handler directly
        mgr._handle_publish(first_byte, body)

        # Should have delivered the event
        assert not q.empty()
        event = q.get_nowait()
        assert isinstance(event, MessageEvent)
        assert event.thread_id == "T1"
        assert event.text == "hey"
        assert event.user_id == 99

        mgr.router.unsubscribe("T1", q)

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


class TestMQTTManagerWaitForMessage:
    def test_returns_message_event(self) -> None:
        """wait_for_message returns when a MessageEvent arrives."""
        mgr = MQTTManager()
        mock_conn = MagicMock()
        mock_conn.is_connected = True
        mgr._conn = mock_conn
        mgr._stop_event.clear()

        event = MessageEvent(
            thread_id="T1",
            item_id="I1",
            user_id=5,
            text="hello",
            item_type="text",
            timestamp=0,
        )

        # Deliver event from another thread after a short delay
        def deliver() -> None:
            time.sleep(0.05)
            mgr.router.deliver(event)

        t = threading.Thread(target=deliver)
        t.start()

        result = mgr.wait_for_message("T1", timeout=5)
        t.join()
        assert result is event

    def test_returns_none_on_timeout(self) -> None:
        """wait_for_message returns None when timeout expires."""
        mgr = MQTTManager()
        mock_conn = MagicMock()
        mock_conn.is_connected = True
        mgr._conn = mock_conn
        mgr._stop_event.clear()

        result = mgr.wait_for_message("T1", timeout=0.1)
        assert result is None

    def test_returns_none_when_disconnected(self) -> None:
        """wait_for_message returns None if connection is lost."""
        mgr = MQTTManager()
        mock_conn = MagicMock()
        mock_conn.is_connected = False
        mgr._conn = mock_conn

        result = mgr.wait_for_message("T1", timeout=5)
        assert result is None

    def test_skips_non_message_events(self) -> None:
        """wait_for_message ignores SeenEvent etc., only returns MessageEvent."""
        from instagram_mcp.mqtt.events import SeenEvent

        mgr = MQTTManager()
        mock_conn = MagicMock()
        mock_conn.is_connected = True
        mgr._conn = mock_conn
        mgr._stop_event.clear()

        seen = SeenEvent(thread_id="T1", user_id=2, item_id="I1", timestamp=100)
        msg = MessageEvent(
            thread_id="T1",
            item_id="I2",
            user_id=3,
            text="hi",
            item_type="text",
            timestamp=200,
        )

        def deliver() -> None:
            time.sleep(0.05)
            mgr.router.deliver(seen)
            time.sleep(0.05)
            mgr.router.deliver(msg)

        t = threading.Thread(target=deliver)
        t.start()

        result = mgr.wait_for_message("T1", timeout=5)
        t.join()
        assert result is msg


class TestMQTTManagerCollectEvents:
    def test_collects_within_window(self) -> None:
        """collect_events gathers events during the time window."""
        mgr = MQTTManager()

        event1 = MessageEvent(
            thread_id="T1",
            item_id="I1",
            user_id=1,
            text="a",
            item_type="text",
            timestamp=0,
        )
        event2 = MessageEvent(
            thread_id="T1",
            item_id="I2",
            user_id=1,
            text="b",
            item_type="text",
            timestamp=1,
        )

        def deliver() -> None:
            time.sleep(0.02)
            mgr.router.deliver(event1)
            time.sleep(0.02)
            mgr.router.deliver(event2)

        t = threading.Thread(target=deliver)
        t.start()

        events = mgr.collect_events("T1", window=0.5)
        t.join()
        assert len(events) == 2
        assert events[0] is event1
        assert events[1] is event2

    def test_empty_on_no_events(self) -> None:
        mgr = MQTTManager()
        events = mgr.collect_events("T1", window=0.1)
        assert events == []


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
