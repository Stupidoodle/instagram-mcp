"""Raw MQTToT connection over TLS.

Handles the low-level MQTT 3.1.1 packet framing with Instagram's custom
MQTToT protocol name and Thrift-encoded CONNECT payloads. Uses raw SSL
sockets (not paho-mqtt) to avoid v2 binary client_id bugs.
"""

from __future__ import annotations

import contextlib
import json
import logging
import socket
import ssl
import struct
import zlib

import certifi

logger = logging.getLogger("instagram_mcp.mqtt")

# MQTT packet types
CONNECT = 1
CONNACK = 2
PUBLISH = 3
PUBACK = 4
PINGREQ = 12
PINGRESP = 13
DISCONNECT = 14

# Instagram MQTT broker
MQTT_HOST = "edge-mqtt.facebook.com"
MQTT_PORT = 443


class MQTToTConnection:
    """Raw SSL socket connection to Instagram's MQTT broker.

    Handles MQTT 3.1.1 packet framing with the custom MQTToT protocol.
    """

    def __init__(self) -> None:
        self._sock: ssl.SSLSocket | None = None

    @property
    def is_connected(self) -> bool:
        """Check if the socket is open."""
        return self._sock is not None

    def connect(
        self,
        client_id_payload: bytes,
        keepalive: int = 60,
        host: str = MQTT_HOST,
        port: int = MQTT_PORT,
    ) -> int:
        """Establish TLS connection and send MQTT CONNECT.

        Args:
            client_id_payload: Zlib-compressed Thrift CONNECT payload.
            keepalive: MQTT keepalive interval in seconds.
            host: MQTT broker hostname.
            port: MQTT broker port.

        Returns:
            CONNACK return code (0 = success).

        Raises:
            ConnectionError: If TCP/TLS connection fails.
            RuntimeError: If CONNACK is not received or returns error.
        """
        ctx = ssl.create_default_context(cafile=certifi.where())
        raw_sock = socket.create_connection((host, port), timeout=10)
        self._sock = ctx.wrap_socket(raw_sock, server_hostname=host)
        logger.debug("TLS connected: %s", self._sock.version())

        # Build MQTT CONNECT packet
        proto_name = b"MQTToT"
        variable_header = struct.pack(
            f"!H{len(proto_name)}sBBH",
            len(proto_name),
            proto_name,
            3,  # protocol_version
            0xC2,  # connect_flags (clean_session + password)
            keepalive,
        )
        remaining = len(variable_header) + len(client_id_payload)

        packet = bytearray([0x10])  # CONNECT packet type
        self._encode_remaining_length(packet, remaining)
        packet.extend(variable_header)
        packet.extend(client_id_payload)
        self._sock.sendall(bytes(packet))

        # Read CONNACK
        self._sock.settimeout(10)
        result = self.read_packet()
        if result is None:
            self.disconnect()
            raise RuntimeError("No CONNACK received")

        ptype, _flags, body = result
        if ptype != CONNACK:
            self.disconnect()
            raise RuntimeError(f"Expected CONNACK, got packet type {ptype}")

        rc = body[1] if len(body) >= 2 else -1
        if rc != 0:
            self.disconnect()
            raise RuntimeError(f"CONNACK rejected: rc={rc}")

        logger.info("MQTT connected (rc=0)")
        return rc

    def publish(
        self,
        topic_id: int,
        payload_dict: dict,
        qos: int = 1,
        packet_id: int = 1,
    ) -> None:
        """Send a PUBLISH packet with zlib-compressed JSON payload.

        Args:
            topic_id: Numeric topic ID (e.g. 134 for /ig_sub_iris).
            payload_dict: Dict to JSON-encode and zlib-compress.
            qos: MQTT QoS level (0 or 1).
            packet_id: MQTT packet identifier (for QoS 1).
        """
        if not self._sock:
            raise RuntimeError("Not connected")

        topic = str(topic_id).encode("utf-8")
        payload = zlib.compress(json.dumps(payload_dict).encode("utf-8"), 9)

        remaining = 2 + len(topic) + len(payload)
        if qos > 0:
            remaining += 2  # packet ID

        flags = 0x30 | ((qos & 0x03) << 1)
        packet = bytearray([flags])
        self._encode_remaining_length(packet, remaining)
        packet.extend(struct.pack("!H", len(topic)))
        packet.extend(topic)
        if qos > 0:
            packet.extend(struct.pack("!H", packet_id))
        packet.extend(payload)
        self._sock.sendall(bytes(packet))
        logger.debug("Published to topic %d (%dB)", topic_id, len(payload))

    def read_packet(self) -> tuple[int, int, bytes] | None:
        """Read one MQTT packet from the socket.

        Returns:
            Tuple of (packet_type, first_byte, body) or None on timeout.
        """
        if not self._sock:
            return None

        try:
            header = self._sock.recv(1)
            if not header:
                # Remote end closed the connection — mark socket dead immediately
                logger.debug("recv returned empty bytes, connection closed by remote")
                self._sock = None
                return None
            first_byte = header[0]
            ptype = (first_byte >> 4) & 0x0F

            # Decode remaining length (variable-length encoding)
            remaining = 0
            multiplier = 1
            while True:
                byte_data = self._sock.recv(1)
                if not byte_data:
                    logger.debug("Connection closed mid-packet (remaining length)")
                    self._sock = None
                    return None
                remaining += (byte_data[0] & 0x7F) * multiplier
                multiplier *= 128
                if not (byte_data[0] & 0x80):
                    break

            # Read the body
            body = bytearray()
            while len(body) < remaining:
                chunk = self._sock.recv(remaining - len(body))
                if not chunk:
                    logger.debug("Connection closed mid-packet (body)")
                    self._sock = None
                    break
                body.extend(chunk)

            return ptype, first_byte, bytes(body)

        except TimeoutError:
            return None

    def send_puback(self, packet_id: int) -> None:
        """Send PUBACK for a QoS 1 message."""
        if self._sock:
            self._sock.sendall(struct.pack("!BBH", 0x40, 0x02, packet_id))

    def send_pingreq(self) -> None:
        """Send PINGREQ keepalive."""
        if self._sock:
            self._sock.sendall(b"\xc0\x00")
            logger.debug("Sent PINGREQ")

    def send_pingresp(self) -> None:
        """Send PINGRESP in response to server PINGREQ."""
        if self._sock:
            self._sock.sendall(b"\xd0\x00")

    def disconnect(self) -> None:
        """Send DISCONNECT and close the socket."""
        if self._sock:
            with contextlib.suppress(OSError):
                self._sock.sendall(b"\xe0\x00")  # DISCONNECT
            with contextlib.suppress(OSError):
                self._sock.close()
            self._sock = None
            logger.info("MQTT disconnected")

    def set_timeout(self, timeout: float) -> None:
        """Set socket read timeout."""
        if self._sock:
            self._sock.settimeout(timeout)

    @staticmethod
    def _encode_remaining_length(buf: bytearray, length: int) -> None:
        """Encode MQTT remaining length into variable-length format."""
        while True:
            byte = length % 128
            length //= 128
            buf.append(byte | 0x80 if length > 0 else byte)
            if length == 0:
                break
