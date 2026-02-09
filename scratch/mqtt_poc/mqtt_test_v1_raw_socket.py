"""Minimal MQTToT proof-of-concept: connect to Instagram's MQTT broker."""

import json
import socket
import ssl
import struct
import time
import zlib

# ── Thrift Compact Protocol encoder ──────────────────────────────────────────


class ThriftWriter:
    """Minimal Thrift Compact Protocol encoder."""

    def __init__(self):
        self._buf = bytearray()
        self._last_field_id = 0
        self._field_stack: list[int] = []

    def _write_varint(self, value: int) -> None:
        while True:
            byte = value & 0x7F
            value >>= 7
            if value:
                self._buf.append(byte | 0x80)
            else:
                self._buf.append(byte)
                break

    def _zigzag(self, value: int, bits: int = 64) -> int:
        return (value << 1) ^ (value >> (bits - 1))

    def _write_field_header(self, field_id: int, thrift_type: int) -> None:
        delta = field_id - self._last_field_id
        if 0 < delta <= 15:
            self._buf.append((delta << 4) | thrift_type)
        else:
            self._buf.append(thrift_type)
            self._write_varint(self._zigzag(field_id, 16))
        self._last_field_id = field_id

    def write_bool(self, field_id: int, value: bool) -> None:
        # Compact protocol encodes bool in the type nibble: 1=TRUE, 2=FALSE
        self._write_field_header(field_id, 1 if value else 2)

    def write_byte(self, field_id: int, value: int) -> None:
        self._write_field_header(field_id, 3)  # BYTE type
        self._buf.append(value & 0xFF)

    def write_i16(self, field_id: int, value: int) -> None:
        self._write_field_header(field_id, 4)  # I16 type
        self._write_varint(self._zigzag(value, 16))

    def write_i32(self, field_id: int, value: int) -> None:
        self._write_field_header(field_id, 5)  # I32 type
        self._write_varint(self._zigzag(value, 32))

    def write_i64(self, field_id: int, value: int) -> None:
        self._write_field_header(field_id, 6)  # I64 type
        self._write_varint(self._zigzag(value, 64))

    def write_string(self, field_id: int, value: str) -> None:
        data = value.encode("utf-8")
        self._write_field_header(field_id, 8)  # BINARY/STRING type
        self._write_varint(len(data))
        self._buf.extend(data)

    def write_list_i32(self, field_id: int, values: list[int]) -> None:
        self._write_field_header(field_id, 9)  # LIST type
        # List header: if len < 15, pack (len << 4) | elem_type in one byte
        if len(values) < 15:
            self._buf.append((len(values) << 4) | 5)  # 5 = I32
        else:
            self._buf.append(0xF0 | 5)
            self._write_varint(len(values))
        for v in values:
            self._write_varint(self._zigzag(v, 32))

    def write_map_str_str(self, field_id: int, mapping: dict[str, str]) -> None:
        self._write_field_header(field_id, 11)  # MAP type
        if not mapping:
            self._buf.append(0)
            return
        self._write_varint(len(mapping))
        self._buf.append(0x88)  # key_type=BINARY(8) << 4 | val_type=BINARY(8)
        for k, v in mapping.items():
            k_bytes = k.encode("utf-8")
            self._write_varint(len(k_bytes))
            self._buf.extend(k_bytes)
            v_bytes = v.encode("utf-8")
            self._write_varint(len(v_bytes))
            self._buf.extend(v_bytes)

    def write_struct_begin(self, field_id: int) -> None:
        self._write_field_header(field_id, 12)  # STRUCT type
        self._field_stack.append(self._last_field_id)
        self._last_field_id = 0

    def write_stop(self) -> None:
        self._buf.append(0)  # STOP byte
        if self._field_stack:
            self._last_field_id = self._field_stack.pop()

    def getvalue(self) -> bytes:
        return bytes(self._buf)


# ── Build the CONNECT payload ────────────────────────────────────────────────


def build_connect_payload(session: dict) -> bytes:
    """Build the Thrift-encoded, zlib-compressed MQTToT CONNECT payload."""
    auth_data = session["authorization_data"]
    uuids = session["uuids"]
    device = session["device_settings"]
    user_agent = session["user_agent"]
    user_id = int(auth_data["ds_user_id"])
    phone_id = uuids["phone_id"]
    session_id = auth_data["sessionid"]

    # Build the Bearer token the same way instagrapi does
    token_data = json.dumps(
        {
            "ds_user_id": auth_data["ds_user_id"],
            "sessionid": session_id,
            "should_use_header_over_cookies": auth_data.get("should_use_header_over_cookies", True),
        },
        separators=(",", ":"),
    )
    import base64

    bearer_token = f"Bearer IGT:2:{base64.b64encode(token_data.encode()).decode()}"
    password = f"authorization={bearer_token}"

    # Subscribe topics
    subscribe_topics = [88, 135, 149, 150, 133, 146, 34]

    # Build RealtimeClientInfo (field 4)
    w = ThriftWriter()

    # Field 1: client_identifier (phone_id[:20])
    w.write_string(1, phone_id[:20])

    # Field 4: client_info struct
    w.write_struct_begin(4)
    w.write_i64(1, user_id)  # user_id
    w.write_string(2, user_agent)  # user_agent
    w.write_i64(3, 183)  # client_capabilities
    w.write_i64(4, 0)  # endpoint_capabilities
    w.write_i32(5, 1)  # publish_format
    w.write_bool(6, True)  # no_automatic_foreground
    w.write_bool(7, False)  # make_user_available_in_foreground
    w.write_string(8, phone_id)  # device_id
    w.write_bool(9, False)  # is_initially_foreground
    w.write_i32(10, 1)  # network_type (WiFi)
    w.write_i32(11, -1)  # network_subtype
    w.write_i64(12, int(time.time() * 1000) & 0xFFFFFFFF)  # mqtt_session_id
    w.write_list_i32(14, subscribe_topics)  # subscribe_topics
    w.write_string(15, "cookie_auth")  # client_type
    w.write_i64(16, 567067343352427)  # app_id
    w.write_string(20, "")  # device_secret
    w.write_byte(21, 3)  # client_stack
    w.write_stop()  # end client_info struct

    # Field 5: password
    w.write_string(5, password)

    # Field 8: app_specific_info
    w.write_map_str_str(
        8,
        {
            "capabilities": "3brTvx0=",
            "app_version": device["app_version"],
            "User-Agent": user_agent,
            "Accept-Language": "en-US",
            "platform": "android",
            "ig_mqtt_route": "django",
            "pubsub_msg_type_blacklist": "direct, typing_type",
            "auth_cache_enabled": "1",
        },
    )

    # STOP byte for outer struct
    w.write_stop()

    payload = w.getvalue()
    compressed = zlib.compress(payload, 9)
    print(f"Thrift payload: {len(payload)} bytes -> compressed: {len(compressed)} bytes")
    return compressed


# ── Build the MQTT CONNECT packet ───────────────────────────────────────────


def build_mqttot_connect_packet(client_id: bytes, keepalive: int = 60) -> bytes:
    """Build a raw MQTToT CONNECT packet."""
    protocol_name = b"MQTToT"
    proto_ver = 3  # MQTTv31
    connect_flags = 0xC2  # username(0x80) + password(0x40) + clean_session(0x02)

    # Variable header: protocol name + version + flags + keepalive
    var_header = struct.pack(
        f"!H{len(protocol_name)}sBBH",
        len(protocol_name),
        protocol_name,
        proto_ver,
        connect_flags,
        keepalive,
    )

    # Payload: just the raw client_id bytes (NO length prefix - this is the MQTToT deviation)
    remaining_length = len(var_header) + len(client_id)

    # Build fixed header
    packet = bytearray()
    packet.append(0x10)  # CONNECT packet type

    # Encode remaining length (variable-length encoding)
    rl = remaining_length
    while True:
        byte = rl % 128
        rl = rl // 128
        if rl > 0:
            byte |= 0x80
        packet.append(byte)
        if rl == 0:
            break

    packet.extend(var_header)
    packet.extend(client_id)

    return bytes(packet)


# ── Parse CONNACK ────────────────────────────────────────────────────────────


def parse_connack(data: bytes) -> dict:
    """Parse MQTT CONNACK response."""
    if len(data) < 4:
        return {"error": f"Too short: {len(data)} bytes", "raw": data.hex()}

    packet_type = (data[0] >> 4) & 0x0F
    if packet_type != 2:  # CONNACK = 2
        return {"error": f"Not CONNACK, type={packet_type}", "raw": data[:20].hex()}

    remaining_length = data[1]
    session_present = data[2] & 0x01
    return_code = data[3]

    result = {
        "type": "CONNACK",
        "session_present": bool(session_present),
        "return_code": return_code,
        "return_code_meaning": {
            0: "Connection Accepted",
            1: "Unacceptable Protocol Version",
            2: "Identifier Rejected",
            3: "Server Unavailable",
            4: "Bad Username or Password",
            5: "Not Authorized",
        }.get(return_code, f"Unknown ({return_code})"),
    }

    # MQTToT CONNACK may have extra payload
    if remaining_length > 2:
        extra = data[4 : 4 + remaining_length - 2]
        result["extra_payload"] = extra.hex()
        result["extra_length"] = len(extra)

    return result


# ── Test it! ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Load session
    with open("/Users/bryantran/informatik/instagram-mcp/.instagram_session") as f:
        session = json.load(f)

    print("=== Building CONNECT payload ===")
    client_id = build_connect_payload(session)

    print("\n=== Building CONNECT packet ===")
    packet = build_mqttot_connect_packet(client_id, keepalive=60)
    print(f"Total packet size: {len(packet)} bytes")

    print("\n=== Connecting to edge-mqtt.facebook.com:443 ===")
    import certifi

    ctx = ssl.create_default_context(cafile=certifi.where())
    sock = socket.create_connection(("edge-mqtt.facebook.com", 443), timeout=10)
    ssl_sock = ctx.wrap_socket(sock, server_hostname="edge-mqtt.facebook.com")
    print(f"TLS connected: {ssl_sock.version()}")

    print("\n=== Sending CONNECT ===")
    ssl_sock.sendall(packet)

    print("=== Waiting for CONNACK ===")
    ssl_sock.settimeout(10)
    response = ssl_sock.recv(1024)
    print(f"Received {len(response)} bytes")

    result = parse_connack(response)
    print(f"CONNACK: {json.dumps(result, indent=2)}")

    if result.get("return_code") == 0:
        print("\n✅ CONNECTION ACCEPTED!")

        # Try waiting for any message for a few seconds
        print("\n=== Listening for messages (5 seconds) ===")
        ssl_sock.settimeout(5)
        try:
            while True:
                data = ssl_sock.recv(4096)
                if not data:
                    print("Connection closed by server")
                    break
                ptype = (data[0] >> 4) & 0x0F
                print(f"Received packet type={ptype}, {len(data)} bytes")
                if ptype == 3:  # PUBLISH
                    # Try to decompress payload
                    # Skip fixed header (type + remaining_length)
                    idx = 1
                    multiplier = 1
                    remaining = 0
                    while True:
                        byte = data[idx]
                        remaining += (byte & 0x7F) * multiplier
                        multiplier *= 128
                        idx += 1
                        if not (byte & 0x80):
                            break
                    # Topic: 2-byte length + topic string
                    topic_len = struct.unpack("!H", data[idx : idx + 2])[0]
                    topic = data[idx + 2 : idx + 2 + topic_len].decode("utf-8", errors="replace")
                    payload_start = idx + 2 + topic_len
                    # QoS 1 has packet ID
                    qos = (data[0] >> 1) & 0x03
                    if qos > 0:
                        payload_start += 2
                    payload = data[payload_start:]
                    print(f"  Topic: {topic}")
                    try:
                        decompressed = zlib.decompress(payload)
                        text = decompressed.decode("utf-8", errors="replace")
                        print(f"  Payload ({len(decompressed)} bytes): {text[:500]}")
                    except Exception:
                        print(f"  Raw payload ({len(payload)} bytes): {payload[:100].hex()}")
                elif ptype == 13:  # PINGREQ
                    print("  -> Sending PINGRESP")
                    ssl_sock.sendall(b"\xd0\x00")
        except TimeoutError:
            print("(timeout - no more messages)")
    else:
        print(f"\n❌ CONNECTION REJECTED: {result.get('return_code_meaning')}")

    ssl_sock.close()
    print("\nDone.")
