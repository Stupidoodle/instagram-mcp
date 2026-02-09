"""Instagram MQTToT Protocol - Python Implementation Starter Code

Based on analysis of Nerixyz/instagram_mqtt TypeScript implementation.
See MQTTOT_PROTOCOL_ANALYSIS.md for complete documentation.
"""

import json
import socket
import ssl
import time
import zlib
from dataclasses import dataclass

# ============================================================================
# Thrift Compact Protocol Writer
# ============================================================================


class ThriftTypes:
    """Thrift Compact Protocol type constants."""

    STOP = 0x00
    TRUE = 0x01
    FALSE = 0x02
    BYTE = 0x03
    INT_16 = 0x04
    INT_32 = 0x05
    INT_64 = 0x06
    DOUBLE = 0x07
    BINARY = 0x08
    LIST = 0x09
    SET = 0x0A
    MAP = 0x0B
    STRUCT = 0x0C


class ThriftWriter:
    """Thrift Compact Protocol binary writer.

    Implements the encoding used by Instagram's MQTToT CONNECT payload.
    """

    def __init__(self):
        self.buffer = bytearray()
        self.field = 0
        self.stack = []

    def write_field_header(self, field_id: int, field_type: int):
        """Write Thrift field header with delta encoding."""
        delta = field_id - self.field
        if 0 < delta <= 15:
            # Use delta encoding: (delta << 4) | type
            self.buffer.append((delta << 4) | field_type)
        else:
            # Full encoding: type + field_id (zigzag varint)
            self.buffer.append(field_type)
            self.write_varint(self.zigzag_encode(field_id, 16))
        self.field = field_id

    def write_varint(self, value: int):
        """Write variable-length integer (continuation bit encoding)."""
        if value < 0:
            # Handle negative numbers
            value = value & ((1 << 64) - 1)

        while True:
            byte = value & 0x7F
            value >>= 7
            if value != 0:
                self.buffer.append(byte | 0x80)  # Continuation bit
            else:
                self.buffer.append(byte)
                break

    @staticmethod
    def zigzag_encode(n: int, bits: int) -> int:
        """ZigZag encoding: maps signed integers to unsigned.

        Example:
          0 -> 0
          -1 -> 1
          1 -> 2
          -2 -> 3
        """
        mask = (1 << bits) - 1
        return ((n << 1) ^ (n >> (bits - 1))) & mask

    def write_bool(self, field_id: int, value: bool):
        """Write boolean field (type IS the value)."""
        self.write_field_header(field_id, ThriftTypes.TRUE if value else ThriftTypes.FALSE)

    def write_byte(self, field_id: int, value: int):
        """Write signed byte field."""
        self.write_field_header(field_id, ThriftTypes.BYTE)
        self.buffer.append(value & 0xFF)

    def write_i16(self, field_id: int, value: int):
        """Write 16-bit integer field (zigzag + varint)."""
        self.write_field_header(field_id, ThriftTypes.INT_16)
        self.write_varint(self.zigzag_encode(value, 16))

    def write_i32(self, field_id: int, value: int):
        """Write 32-bit integer field (zigzag + varint)."""
        self.write_field_header(field_id, ThriftTypes.INT_32)
        self.write_varint(self.zigzag_encode(value, 32))

    def write_i64(self, field_id: int, value: int):
        """Write 64-bit integer field (zigzag + varint)."""
        self.write_field_header(field_id, ThriftTypes.INT_64)
        zigzag = ((value << 1) ^ (value >> 63)) & ((1 << 64) - 1)
        self.write_varint(zigzag)

    def write_string(self, field_id: int, value: str):
        """Write UTF-8 string field."""
        self.write_field_header(field_id, ThriftTypes.BINARY)
        utf8 = value.encode("utf-8")
        self.write_varint(len(utf8))
        self.buffer.extend(utf8)

    def write_binary(self, field_id: int, value: bytes):
        """Write binary data field."""
        self.write_field_header(field_id, ThriftTypes.BINARY)
        self.write_varint(len(value))
        self.buffer.extend(value)

    def write_list_i32(self, field_id: int, values: list[int]):
        """Write list of int32 values."""
        self.write_field_header(field_id, ThriftTypes.LIST)
        size = len(values)

        if size < 15:
            # Compact: (size << 4) | element_type
            self.buffer.append((size << 4) | ThriftTypes.INT_32)
        else:
            # Full: 0xF0 | element_type, then size
            self.buffer.append(0xF0 | ThriftTypes.INT_32)
            self.write_varint(size)

        # Write elements (no field headers)
        for val in values:
            self.write_varint(self.zigzag_encode(val, 32))

    def write_map_string_string(self, field_id: int, pairs: dict[str, str]):
        """Write map of string -> string."""
        self.write_field_header(field_id, ThriftTypes.MAP)
        size = len(pairs)

        if size == 0:
            self.buffer.append(0)
        else:
            self.write_varint(size)
            # Key type and value type: (BINARY << 4) | BINARY = 0x88
            self.buffer.append(0x88)

            # Write key-value pairs
            for key, value in pairs.items():
                # Write key
                utf8_key = key.encode("utf-8")
                self.write_varint(len(utf8_key))
                self.buffer.extend(utf8_key)
                # Write value
                utf8_val = value.encode("utf-8")
                self.write_varint(len(utf8_val))
                self.buffer.extend(utf8_val)

    def write_struct_begin(self, field_id: int):
        """Begin nested struct (saves field counter to stack)."""
        self.write_field_header(field_id, ThriftTypes.STRUCT)
        self.stack.append(self.field)
        self.field = 0

    def write_struct_end(self):
        """End nested struct (writes STOP and restores field counter)."""
        self.buffer.append(ThriftTypes.STOP)
        if self.stack:
            self.field = self.stack.pop()

    def write_stop(self):
        """Write STOP marker (end of message)."""
        self.buffer.append(ThriftTypes.STOP)

    def get_bytes(self) -> bytes:
        """Get final encoded bytes."""
        return bytes(self.buffer)


# ============================================================================
# MQTToT Connection Payload Builder
# ============================================================================


@dataclass
class MQTToTConnectionData:
    """Instagram MQTToT connection parameters."""

    user_id: int
    session_id: str
    device_id: str
    user_agent: str
    app_version: str
    capabilities_header: str
    language: str = "en_US"


def build_mqttot_connect_payload(data: MQTToTConnectionData) -> bytes:
    """Build Instagram MQTToT CONNECT payload (compressed Thrift).

    Returns:
        Compressed binary payload ready for CONNECT packet.
    """
    writer = ThriftWriter()

    # Field 1: clientIdentifier (first 20 chars of device_id)
    writer.write_string(1, data.device_id[:20])

    # Field 4: clientInfo (nested struct)
    writer.write_struct_begin(4)

    # clientInfo.userId (field 1)
    writer.write_i64(1, data.user_id)

    # clientInfo.userAgent (field 2)
    writer.write_string(2, data.user_agent)

    # clientInfo.clientCapabilities (field 3)
    writer.write_i64(3, 183)

    # clientInfo.endpointCapabilities (field 4)
    writer.write_i64(4, 0)

    # clientInfo.publishFormat (field 5)
    writer.write_i32(5, 1)

    # clientInfo.noAutomaticForeground (field 6)
    writer.write_bool(6, False)

    # clientInfo.makeUserAvailableInForeground (field 7)
    writer.write_bool(7, True)

    # clientInfo.deviceId (field 8)
    writer.write_string(8, data.device_id)

    # clientInfo.isInitiallyForeground (field 9)
    writer.write_bool(9, True)

    # clientInfo.networkType (field 10)
    writer.write_i32(10, 1)

    # clientInfo.networkSubtype (field 11)
    writer.write_i32(11, 0)

    # clientInfo.clientMqttSessionId (field 12)
    # Use current timestamp & 0xffffffff
    session_id_int = int(time.time() * 1000) & 0xFFFFFFFF
    writer.write_i64(12, session_id_int)

    # clientInfo.subscribeTopics (field 14)
    # CRITICAL: Must include 146 for DMs!
    writer.write_list_i32(14, [88, 135, 149, 150, 133, 146])

    # clientInfo.clientType (field 15)
    writer.write_string(15, "cookie_auth")

    # clientInfo.appId (field 16)
    writer.write_i64(16, 567067343352427)

    # clientInfo.deviceSecret (field 20)
    writer.write_string(20, "")

    # clientInfo.clientStack (field 21)
    writer.write_byte(21, 3)

    writer.write_struct_end()  # End clientInfo

    # Field 5: password (SESSION AUTHENTICATION!)
    writer.write_string(5, f"sessionid={data.session_id}")

    # Field 10: appSpecificInfo (map)
    app_info = {
        "app_version": data.app_version,
        "X-IG-Capabilities": data.capabilities_header,
        "everclear_subscriptions": json.dumps(
            {
                "inapp_notification_subscribe_comment": "17899377895239777",
                "inapp_notification_subscribe_comment_mention_and_reply": "17899377895239777",
                "video_call_participant_state_delivery": "17977239895057311",
                "presence_subscribe": "17846944882223835",
            }
        ),
        "User-Agent": data.user_agent,
        "Accept-Language": data.language.replace("_", "-"),
        "platform": "android",
        "ig_mqtt_route": "django",
        "pubsub_msg_type_blacklist": "direct, typing_type",
        "auth_cache_enabled": "0",
    }
    writer.write_map_string_string(10, app_info)

    writer.write_stop()  # End top-level struct

    # Compress with zlib level 9
    thrift_bytes = writer.get_bytes()
    compressed = zlib.compress(thrift_bytes, level=9)

    return compressed


# ============================================================================
# MQTToT CONNECT Packet Builder
# ============================================================================


def build_mqttot_connect_packet(payload: bytes, keep_alive: int = 20) -> bytes:
    """Build MQTToT CONNECT packet.

    Args:
        payload: Compressed Thrift payload
        keep_alive: Keep-alive interval in seconds (default: 20)

    Returns:
        Complete CONNECT packet bytes ready to send.
    """
    # Variable header + payload
    var_header = bytearray()

    # Protocol name: "MQTToT" (length-prefixed)
    protocol_name = b"MQTToT"
    var_header.append(0x00)
    var_header.append(len(protocol_name))
    var_header.extend(protocol_name)

    # Protocol level: 3 (MQTT 3.1.1)
    var_header.append(3)

    # Connect flags: 194 (0xC2 = 0b11000010)
    # Bit 7: User Name Flag = 1
    # Bit 6: Password Flag = 1
    # Bit 5: Will Retain = 0
    # Bit 4-3: Will QoS = 0
    # Bit 2: Will Flag = 0
    # Bit 1: Clean Session = 1
    # Bit 0: Reserved = 0
    var_header.append(194)

    # Keep-alive: 16-bit big-endian
    var_header.append((keep_alive >> 8) & 0xFF)
    var_header.append(keep_alive & 0xFF)

    # Payload (compressed Thrift)
    var_header.extend(payload)

    # Build final packet with fixed header
    packet = bytearray()

    # Fixed header: CONNECT packet type (0x10)
    packet.append(0x10)

    # Remaining length (varint)
    remaining_length = len(var_header)
    while True:
        byte = remaining_length % 128
        remaining_length //= 128
        if remaining_length > 0:
            packet.append(byte | 0x80)
        else:
            packet.append(byte)
            break

    packet.extend(var_header)

    return bytes(packet)


# ============================================================================
# MQTToT CONNACK Parser
# ============================================================================


@dataclass
class ConnAck:
    """CONNACK packet data."""

    ack_flags: int
    return_code: int
    payload: bytes


def parse_connack(data: bytes) -> ConnAck:
    """Parse MQTToT CONNACK packet.

    Args:
        data: Raw packet bytes (after fixed header)

    Returns:
        ConnAck object with ack flags, return code, and optional payload.
    """
    pos = 0

    # Skip packet type (assumed already parsed)
    pos += 1

    # Parse remaining length (varint)
    remaining_length = 0
    shift = 0
    while True:
        byte = data[pos]
        pos += 1
        remaining_length |= (byte & 0x7F) << shift
        if (byte & 0x80) == 0:
            break
        shift += 7

    # Parse CONNACK fields
    ack_flags = data[pos]
    pos += 1

    return_code = data[pos]
    pos += 1

    # Read optional payload (length-prefixed string)
    payload = b""
    if pos < len(data):
        # Read string length (varint)
        length = 0
        shift = 0
        while pos < len(data):
            byte = data[pos]
            pos += 1
            length |= (byte & 0x7F) << shift
            if (byte & 0x80) == 0:
                break
            shift += 7

        payload = data[pos : pos + length]

    return ConnAck(ack_flags=ack_flags, return_code=return_code, payload=payload)


# ============================================================================
# Instagram Realtime Client
# ============================================================================


class InstagramRealtimeClient:
    """Instagram Realtime MQTT client for receiving DMs.

    Usage:
        from instagrapi import Client

        ig = Client()
        ig.login(username, password)

        realtime = InstagramRealtimeClient(ig)
        realtime.connect()
        realtime.subscribe_iris()

        while True:
            realtime.receive_messages()
    """

    MQTT_HOST = "edge-mqtt.facebook.com"
    MQTT_PORT = 443

    def __init__(self, ig_client):
        """Initialize realtime client.

        Args:
            ig_client: Authenticated instagrapi Client instance
        """
        self.ig = ig_client
        self.sock: ssl.SSLSocket | None = None
        self.connected = False

    def connect(self) -> ConnAck:
        """Connect to Instagram realtime server.

        Returns:
            CONNACK response

        Raises:
            ConnectionError: If connection fails
        """
        # Get session data from instagrapi
        user_id = int(self.ig.user_id)
        # Note: sessionid extraction depends on instagrapi version
        # You may need to adapt this:
        session_id = self.ig.sessionid  # or self.ig.get_settings()['sessionid']
        device_id = self.ig.device_id
        user_agent = self.ig.user_agent

        # Build connection data
        conn_data = MQTToTConnectionData(
            user_id=user_id,
            session_id=session_id,
            device_id=device_id,
            user_agent=user_agent,
            app_version=self.ig.app_version,
            capabilities_header=self.ig.get_capabilities(),  # or self.ig.capabilities_header
            language=self.ig.locale,  # or 'en_US'
        )

        # Build CONNECT payload
        payload = build_mqttot_connect_payload(conn_data)

        # Build CONNECT packet
        connect_packet = build_mqttot_connect_packet(payload, keep_alive=20)

        # Open TLS connection
        raw_sock = socket.create_connection((self.MQTT_HOST, self.MQTT_PORT))
        context = ssl.create_default_context()
        self.sock = context.wrap_socket(raw_sock, server_hostname=self.MQTT_HOST)

        # Send CONNECT
        self.sock.sendall(connect_packet)

        # Receive CONNACK
        connack_data = self.sock.recv(4096)
        connack = parse_connack(connack_data)

        if connack.return_code != 0:
            raise ConnectionError(f"MQTT connection failed with return code: {connack.return_code}")

        self.connected = True
        print(f"Connected! CONNACK payload: {connack.payload.hex() if connack.payload else 'none'}")

        return connack

    def subscribe_iris(self):
        """Subscribe to Iris message sync (required for receiving DMs).

        This must be called after connect() to start receiving messages.
        """
        # Get inbox data for seq_id and snapshot_at_ms
        inbox = self.ig.direct_threads()

        # Note: Field names depend on instagrapi version, may need adaptation
        seq_id = inbox.get("seq_id") or inbox.get("pending_requests_total", 0)
        snapshot_at_ms = inbox.get("snapshot_at_ms") or int(time.time() * 1000)

        # Build subscription payload
        sub_data = {
            "seq_id": seq_id,
            "snapshot_at_ms": snapshot_at_ms,
            "snapshot_app_version": self.ig.app_version,
        }

        # Publish to topic 134 (/ig_sub_iris)
        self.publish(topic_id="134", payload=json.dumps(sub_data).encode("utf-8"))

        print(f"Subscribed to Iris (seq_id={seq_id}, snapshot_at_ms={snapshot_at_ms})")

    def publish(self, topic_id: str, payload: bytes, qos: int = 1):
        """Publish message to topic.

        Args:
            topic_id: MQTT topic ID (e.g., "134")
            payload: Message payload (will be compressed)
            qos: Quality of Service level (0, 1, or 2)
        """
        if not self.connected or not self.sock:
            raise RuntimeError("Not connected")

        # Compress payload
        compressed = zlib.compress(payload, level=9)

        # Build PUBLISH packet
        # (Simplified - full implementation needs packet ID handling for QoS > 0)
        packet = bytearray()

        # Fixed header: PUBLISH (0x30) with QoS flags
        packet.append(0x30 | (qos << 1))

        # Variable header: topic name
        topic_bytes = topic_id.encode("utf-8")

        # Calculate remaining length
        remaining = 2 + len(topic_bytes) + len(compressed)
        if qos > 0:
            remaining += 2  # Packet ID

        # Write remaining length (varint)
        while True:
            byte = remaining % 128
            remaining //= 128
            if remaining > 0:
                packet.append(byte | 0x80)
            else:
                packet.append(byte)
                break

        # Topic name (length-prefixed)
        packet.append((len(topic_bytes) >> 8) & 0xFF)
        packet.append(len(topic_bytes) & 0xFF)
        packet.extend(topic_bytes)

        # Packet ID (if QoS > 0)
        if qos > 0:
            packet_id = 1  # Simplified - should increment
            packet.append((packet_id >> 8) & 0xFF)
            packet.append(packet_id & 0xFF)

        # Payload
        packet.extend(compressed)

        self.sock.sendall(bytes(packet))

    def receive_messages(self, timeout: float | None = None):
        """Receive and handle incoming messages.

        Args:
            timeout: Socket timeout in seconds (None = blocking)
        """
        if not self.connected or not self.sock:
            raise RuntimeError("Not connected")

        if timeout is not None:
            self.sock.settimeout(timeout)

        # Receive packet
        data = self.sock.recv(4096)

        if not data:
            return

        # Parse packet type
        packet_type = (data[0] >> 4) & 0x0F

        if packet_type == 3:  # PUBLISH
            self._handle_publish(data)
        elif packet_type == 13:  # PINGRESP
            print("Received PINGRESP")
        else:
            print(f"Received packet type: {packet_type}")

    def _handle_publish(self, data: bytes):
        """Handle incoming PUBLISH packet."""
        pos = 1

        # Parse remaining length
        remaining_length = 0
        shift = 0
        while True:
            byte = data[pos]
            pos += 1
            remaining_length |= (byte & 0x7F) << shift
            if (byte & 0x80) == 0:
                break
            shift += 7

        # Parse topic
        topic_length = (data[pos] << 8) | data[pos + 1]
        pos += 2
        topic = data[pos : pos + topic_length].decode("utf-8")
        pos += topic_length

        # Parse payload
        payload = data[pos:]

        # Decompress if needed
        if payload and payload[0] == 0x78:  # zlib magic byte
            payload = zlib.decompress(payload)

        print(f"Received message on topic {topic}: {len(payload)} bytes")

        # Handle based on topic
        if topic == "146":  # /ig_message_sync (DMs!)
            self._handle_dm_message(payload)

    def _handle_dm_message(self, payload: bytes):
        """Handle incoming DM message from topic 146."""
        try:
            messages = json.loads(payload)
            for msg in messages:
                print(f"DM Message: {json.dumps(msg, indent=2)}")

                # Extract relevant data
                if msg.get("data"):
                    for item in msg["data"]:
                        op = item.get("op")  # 'add' or 'replace'
                        path = item.get("path")  # e.g., "/direct_v2/threads/{id}/items/{id}"
                        value = item.get("value")  # Message data

                        if value:
                            print(f"  Operation: {op}")
                            print(f"  Path: {path}")
                            print(f"  Type: {value.get('item_type')}")
                            if value.get("text"):
                                print(f"  Text: {value['text']}")
        except Exception as e:
            print(f"Error parsing DM message: {e}")

    def send_ping(self):
        """Send PINGREQ (keep-alive)."""
        if not self.connected or not self.sock:
            raise RuntimeError("Not connected")

        # PINGREQ packet: 0xC0 0x00
        self.sock.sendall(b"\xc0\x00")

    def disconnect(self):
        """Disconnect from server."""
        if self.sock:
            # Send DISCONNECT packet: 0xE0 0x00
            try:
                self.sock.sendall(b"\xe0\x00")
            except:
                pass
            self.sock.close()
            self.sock = None
        self.connected = False


# ============================================================================
# Example Usage
# ============================================================================

if __name__ == "__main__":
    # Note: This is example code - adapt to your environment

    from instagrapi import Client

    # Login to Instagram
    ig = Client()
    ig.login("username", "password")

    # Create realtime client
    realtime = InstagramRealtimeClient(ig)

    # Connect to MQTT
    connack = realtime.connect()
    print(f"Connected with return code: {connack.return_code}")

    # Subscribe to DM sync
    realtime.subscribe_iris()

    # Receive messages
    import threading
    import time

    def keep_alive():
        """Send PINGREQ every 20 seconds."""
        while realtime.connected:
            time.sleep(20)
            try:
                realtime.send_ping()
                print("Sent PINGREQ")
            except Exception as e:
                print(f"Error sending ping: {e}")
                break

    # Start keep-alive thread
    ping_thread = threading.Thread(target=keep_alive, daemon=True)
    ping_thread.start()

    # Receive messages (blocking)
    print("Listening for messages...")
    try:
        while True:
            realtime.receive_messages(timeout=1.0)
    except KeyboardInterrupt:
        print("\nDisconnecting...")
        realtime.disconnect()
