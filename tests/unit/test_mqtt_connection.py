"""Unit tests for MQTToT connection."""

import struct
from unittest.mock import MagicMock, patch

from instagram_mcp.mqtt.connection import MQTToTConnection


class TestMQTToTConnection:
    def test_initial_state(self) -> None:
        conn = MQTToTConnection()
        assert not conn.is_connected

    def test_encode_remaining_length_small(self) -> None:
        buf = bytearray()
        MQTToTConnection._encode_remaining_length(buf, 100)
        assert buf == bytearray([100])

    def test_encode_remaining_length_medium(self) -> None:
        buf = bytearray()
        MQTToTConnection._encode_remaining_length(buf, 200)
        # 200 = 128*1 + 72 -> 72|0x80=0xC8, 0x01
        assert buf == bytearray([0xC8, 0x01])

    def test_encode_remaining_length_large(self) -> None:
        buf = bytearray()
        MQTToTConnection._encode_remaining_length(buf, 16384)
        # 16384 = 128*128*1 -> 0x80, 0x80, 0x01
        assert buf == bytearray([0x80, 0x80, 0x01])

    def test_disconnect_clears_socket(self) -> None:
        conn = MQTToTConnection()
        mock_sock = MagicMock()
        conn._sock = mock_sock
        assert conn.is_connected

        conn.disconnect()
        assert not conn.is_connected
        mock_sock.sendall.assert_called_once_with(b"\xe0\x00")
        mock_sock.close.assert_called_once()

    def test_disconnect_handles_oserror(self) -> None:
        conn = MQTToTConnection()
        mock_sock = MagicMock()
        mock_sock.sendall.side_effect = OSError("broken pipe")
        conn._sock = mock_sock

        conn.disconnect()  # Should not raise
        assert not conn.is_connected

    def test_send_puback(self) -> None:
        conn = MQTToTConnection()
        mock_sock = MagicMock()
        conn._sock = mock_sock

        conn.send_puback(42)
        expected = struct.pack("!BBH", 0x40, 0x02, 42)
        mock_sock.sendall.assert_called_once_with(expected)

    def test_send_pingreq(self) -> None:
        conn = MQTToTConnection()
        mock_sock = MagicMock()
        conn._sock = mock_sock

        conn.send_pingreq()
        mock_sock.sendall.assert_called_once_with(b"\xc0\x00")

    def test_send_pingresp(self) -> None:
        conn = MQTToTConnection()
        mock_sock = MagicMock()
        conn._sock = mock_sock

        conn.send_pingresp()
        mock_sock.sendall.assert_called_once_with(b"\xd0\x00")

    def test_read_packet_returns_none_when_disconnected(self) -> None:
        conn = MQTToTConnection()
        assert conn.read_packet() is None

    def test_read_packet_timeout(self) -> None:
        conn = MQTToTConnection()
        mock_sock = MagicMock()
        mock_sock.recv.side_effect = TimeoutError("timed out")
        conn._sock = mock_sock

        assert conn.read_packet() is None

    def test_read_packet_connack(self) -> None:
        """Simulate reading a CONNACK packet."""
        conn = MQTToTConnection()
        mock_sock = MagicMock()

        # CONNACK: type=2, remaining_length=2, session_present=0, rc=0
        connack_data = [
            bytes([0x20]),  # First byte: CONNACK (2 << 4)
            bytes([0x02]),  # Remaining length: 2
            bytes([0x00, 0x00]),  # Body: session_present=0, rc=0
        ]
        mock_sock.recv.side_effect = [
            connack_data[0],
            connack_data[1],
            connack_data[2],
        ]
        conn._sock = mock_sock

        result = conn.read_packet()
        assert result is not None
        ptype, _first_byte, body = result
        assert ptype == 2  # CONNACK
        assert body == bytes([0x00, 0x00])

    def test_set_timeout(self) -> None:
        conn = MQTToTConnection()
        mock_sock = MagicMock()
        conn._sock = mock_sock

        conn.set_timeout(5.0)
        mock_sock.settimeout.assert_called_once_with(5.0)

    def test_publish_raises_when_disconnected(self) -> None:
        import pytest

        conn = MQTToTConnection()
        with pytest.raises(RuntimeError, match="Not connected"):
            conn.publish(134, {"test": True})

    def test_publish_sends_packet(self) -> None:
        conn = MQTToTConnection()
        mock_sock = MagicMock()
        conn._sock = mock_sock

        conn.publish(134, {"seq_id": 100}, qos=1, packet_id=1)

        # Verify sendall was called with a bytes-like object
        mock_sock.sendall.assert_called_once()
        packet = mock_sock.sendall.call_args[0][0]
        assert isinstance(packet, bytes)
        # First byte: PUBLISH with QoS 1 -> 0x32
        assert packet[0] == 0x32

    @patch("instagram_mcp.mqtt.connection.socket.create_connection")
    @patch("instagram_mcp.mqtt.connection.ssl.create_default_context")
    def test_connect_success(self, mock_ssl_ctx: MagicMock, mock_create_conn: MagicMock) -> None:
        """Test successful CONNECT + CONNACK flow."""
        mock_raw_sock = MagicMock()
        mock_create_conn.return_value = mock_raw_sock

        mock_ssl_sock = MagicMock()
        mock_ssl_ctx.return_value.wrap_socket.return_value = mock_ssl_sock
        mock_ssl_sock.version.return_value = "TLSv1.3"

        # Simulate CONNACK response
        connack_bytes = [
            bytes([0x20]),  # CONNACK type
            bytes([0x02]),  # remaining length
            bytes([0x00, 0x00]),  # session_present=0, rc=0
        ]
        mock_ssl_sock.recv.side_effect = connack_bytes

        conn = MQTToTConnection()
        rc = conn.connect(b"test_payload", keepalive=60)
        assert rc == 0
        assert conn.is_connected

    @patch("instagram_mcp.mqtt.connection.socket.create_connection")
    @patch("instagram_mcp.mqtt.connection.ssl.create_default_context")
    def test_connect_rejected(self, mock_ssl_ctx: MagicMock, mock_create_conn: MagicMock) -> None:
        """Test CONNACK with non-zero return code."""
        import pytest

        mock_raw_sock = MagicMock()
        mock_create_conn.return_value = mock_raw_sock

        mock_ssl_sock = MagicMock()
        mock_ssl_ctx.return_value.wrap_socket.return_value = mock_ssl_sock
        mock_ssl_sock.version.return_value = "TLSv1.3"

        # CONNACK with rc=3 (Server Unavailable)
        connack_bytes = [
            bytes([0x20]),
            bytes([0x02]),
            bytes([0x00, 0x03]),
        ]
        mock_ssl_sock.recv.side_effect = connack_bytes

        conn = MQTToTConnection()
        with pytest.raises(RuntimeError, match="CONNACK rejected: rc=3"):
            conn.connect(b"test_payload")
        assert not conn.is_connected

    @patch("instagram_mcp.mqtt.connection.socket.create_connection")
    @patch("instagram_mcp.mqtt.connection.ssl.create_default_context")
    def test_connect_no_connack(self, mock_ssl_ctx: MagicMock, mock_create_conn: MagicMock) -> None:
        """Test timeout waiting for CONNACK."""
        import pytest

        mock_raw_sock = MagicMock()
        mock_create_conn.return_value = mock_raw_sock

        mock_ssl_sock = MagicMock()
        mock_ssl_ctx.return_value.wrap_socket.return_value = mock_ssl_sock
        mock_ssl_sock.version.return_value = "TLSv1.3"
        mock_ssl_sock.recv.return_value = b""  # EOF

        conn = MQTToTConnection()
        with pytest.raises(RuntimeError, match="No CONNACK received"):
            conn.connect(b"test_payload")

    @patch("instagram_mcp.mqtt.connection.socket.create_connection")
    @patch("instagram_mcp.mqtt.connection.ssl.create_default_context")
    def test_connect_wrong_packet_type(
        self, mock_ssl_ctx: MagicMock, mock_create_conn: MagicMock
    ) -> None:
        """Test receiving non-CONNACK packet."""
        import pytest

        mock_raw_sock = MagicMock()
        mock_create_conn.return_value = mock_raw_sock

        mock_ssl_sock = MagicMock()
        mock_ssl_ctx.return_value.wrap_socket.return_value = mock_ssl_sock
        mock_ssl_sock.version.return_value = "TLSv1.3"

        # Send a PUBLISH packet instead of CONNACK
        mock_ssl_sock.recv.side_effect = [
            bytes([0x30]),  # PUBLISH type
            bytes([0x02]),
            bytes([0x00, 0x00]),
        ]

        conn = MQTToTConnection()
        with pytest.raises(RuntimeError, match="Expected CONNACK"):
            conn.connect(b"test_payload")

    def test_read_packet_eof_in_header(self) -> None:
        """Socket returns empty bytes during header read."""
        conn = MQTToTConnection()
        mock_sock = MagicMock()
        mock_sock.recv.return_value = b""
        conn._sock = mock_sock

        assert conn.read_packet() is None

    def test_read_packet_eof_in_remaining_length(self) -> None:
        conn = MQTToTConnection()
        mock_sock = MagicMock()
        mock_sock.recv.side_effect = [bytes([0x30]), b""]
        conn._sock = mock_sock

        assert conn.read_packet() is None

    def test_read_packet_eof_in_body(self) -> None:
        conn = MQTToTConnection()
        mock_sock = MagicMock()
        mock_sock.recv.side_effect = [
            bytes([0x30]),  # PUBLISH
            bytes([0x05]),  # remaining length = 5
            b"",  # EOF during body read
        ]
        conn._sock = mock_sock

        result = conn.read_packet()
        # Returns partial body
        assert result is not None
        assert result[2] == b""

    def test_publish_qos0(self) -> None:
        conn = MQTToTConnection()
        mock_sock = MagicMock()
        conn._sock = mock_sock

        conn.publish(134, {"test": True}, qos=0, packet_id=0)
        mock_sock.sendall.assert_called_once()
        packet = mock_sock.sendall.call_args[0][0]
        # First byte: PUBLISH with QoS 0 -> 0x30
        assert packet[0] == 0x30

    def test_send_puback_noop_when_disconnected(self) -> None:
        conn = MQTToTConnection()
        conn.send_puback(1)  # Should not raise

    def test_send_pingreq_noop_when_disconnected(self) -> None:
        conn = MQTToTConnection()
        conn.send_pingreq()  # Should not raise

    def test_send_pingresp_noop_when_disconnected(self) -> None:
        conn = MQTToTConnection()
        conn.send_pingresp()  # Should not raise

    def test_set_timeout_noop_when_disconnected(self) -> None:
        conn = MQTToTConnection()
        conn.set_timeout(5.0)  # Should not raise
