"""MQTToT PoC v3: raw sockets with better debugging + auth variants."""

import base64
import json
import socket
import ssl
import struct
import time
import zlib

import certifi

# ── Thrift Compact Protocol encoder ──────────────────────────────────────────


class ThriftWriter:
    def __init__(self):
        self._buf = bytearray()
        self._last_field_id = 0
        self._field_stack: list[int] = []

    def _write_varint(self, value: int) -> None:
        if value < 0:
            value = value & 0xFFFFFFFFFFFFFFFF
        while True:
            byte = value & 0x7F
            value >>= 7
            if value:
                self._buf.append(byte | 0x80)
            else:
                self._buf.append(byte)
                break

    def _zigzag_i16(self, value: int) -> int:
        return (value << 1) ^ (value >> 15)

    def _zigzag_i32(self, value: int) -> int:
        return (value << 1) ^ (value >> 31)

    def _zigzag_i64(self, value: int) -> int:
        return (value << 1) ^ (value >> 63)

    def _write_field_header(self, field_id: int, thrift_type: int) -> None:
        delta = field_id - self._last_field_id
        if 0 < delta <= 15:
            self._buf.append((delta << 4) | thrift_type)
        else:
            self._buf.append(thrift_type)
            self._write_varint(self._zigzag_i16(field_id))
        self._last_field_id = field_id

    def write_bool(self, field_id: int, value: bool) -> None:
        self._write_field_header(field_id, 1 if value else 2)

    def write_byte(self, field_id: int, value: int) -> None:
        self._write_field_header(field_id, 3)
        self._buf.append(value & 0xFF)

    def write_i32(self, field_id: int, value: int) -> None:
        self._write_field_header(field_id, 5)
        self._write_varint(self._zigzag_i32(value))

    def write_i64(self, field_id: int, value: int) -> None:
        self._write_field_header(field_id, 6)
        self._write_varint(self._zigzag_i64(value))

    def write_string(self, field_id: int, value: str) -> None:
        data = value.encode("utf-8")
        self._write_field_header(field_id, 8)
        self._write_varint(len(data))
        self._buf.extend(data)

    def write_list_i32(self, field_id: int, values: list[int]) -> None:
        self._write_field_header(field_id, 9)
        if len(values) < 15:
            self._buf.append((len(values) << 4) | 5)
        else:
            self._buf.append(0xF0 | 5)
            self._write_varint(len(values))
        for v in values:
            self._write_varint(self._zigzag_i32(v))

    def write_map_str_str(self, field_id: int, mapping: dict[str, str]) -> None:
        self._write_field_header(field_id, 11)
        if not mapping:
            self._buf.append(0)
            return
        self._write_varint(len(mapping))
        self._buf.append(0x88)  # key=BINARY(8) << 4 | val=BINARY(8)
        for k, v in mapping.items():
            k_bytes = k.encode("utf-8")
            self._write_varint(len(k_bytes))
            self._buf.extend(k_bytes)
            v_bytes = v.encode("utf-8")
            self._write_varint(len(v_bytes))
            self._buf.extend(v_bytes)

    def write_struct_begin(self, field_id: int) -> None:
        self._write_field_header(field_id, 12)
        self._field_stack.append(self._last_field_id)
        self._last_field_id = 0

    def write_stop(self) -> None:
        self._buf.append(0)
        if self._field_stack:
            self._last_field_id = self._field_stack.pop()

    def getvalue(self) -> bytes:
        return bytes(self._buf)


# ── Build payload ────────────────────────────────────────────────────────────


def build_payload(session: dict, auth_mode: str = "bearer") -> bytes:
    auth_data = session["authorization_data"]
    uuids = session["uuids"]
    device = session["device_settings"]
    user_agent = session["user_agent"]
    user_id = int(auth_data["ds_user_id"])
    phone_id = uuids["phone_id"]
    session_id = auth_data["sessionid"]

    if auth_mode == "bearer":
        token_json = json.dumps(auth_data, separators=(",", ":"))
        bearer = f"Bearer IGT:2:{base64.b64encode(token_json.encode()).decode()}"
        password = f"authorization={bearer}"
    elif auth_mode == "sessionid":
        password = f"sessionid={session_id}"
    else:
        raise ValueError(f"Unknown auth_mode: {auth_mode}")

    # Full topic list from mautrix
    subscribe_topics = [88, 135, 244, 149, 150, 245, 274, 133, 146, 179, 34]

    w = ThriftWriter()
    w.write_string(1, phone_id[:20])

    w.write_struct_begin(4)
    w.write_i64(1, user_id)
    w.write_string(2, user_agent)
    w.write_i64(3, 183)
    w.write_i64(4, 0)
    w.write_i32(5, 1)
    w.write_bool(6, True)
    w.write_bool(7, False)
    w.write_string(8, phone_id)
    w.write_bool(9, False)
    w.write_i32(10, 1)
    w.write_i32(11, -1)
    w.write_i64(12, int(time.time() * 1000) & 0xFFFFFFFF)
    w.write_list_i32(14, subscribe_topics)
    w.write_string(15, "cookie_auth")
    w.write_i64(16, 567067343352427)
    w.write_string(20, "")
    w.write_byte(21, 3)
    w.write_stop()

    w.write_string(5, password)

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

    w.write_stop()

    raw = w.getvalue()
    compressed = zlib.compress(raw, 9)
    return compressed


def build_connect_packet(client_id: bytes, keepalive: int = 60) -> bytes:
    protocol = b"MQTToT"
    proto_ver = 3
    connect_flags = 0xC2

    var_header = struct.pack(
        f"!H{len(protocol)}sBBH",
        len(protocol),
        protocol,
        proto_ver,
        connect_flags,
        keepalive,
    )

    remaining_length = len(var_header) + len(client_id)
    packet = bytearray()
    packet.append(0x10)  # CONNECT

    # Remaining length (variable-length encoding)
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


def try_connect(session: dict, auth_mode: str) -> int:
    print(f"\n{'=' * 60}")
    print(f"Trying auth_mode='{auth_mode}'")
    print(f"{'=' * 60}")

    client_id = build_payload(session, auth_mode=auth_mode)
    print(f"Payload: {len(client_id)} bytes (compressed)")

    packet = build_connect_packet(client_id)
    print(f"CONNECT packet: {len(packet)} bytes")
    print(f"  Fixed header: {packet[:2].hex()}")
    print(f"  Variable header (first 12 bytes): {packet[2:14].hex()}")

    ctx = ssl.create_default_context(cafile=certifi.where())
    sock = socket.create_connection(("edge-mqtt.facebook.com", 443), timeout=10)
    ssl_sock = ctx.wrap_socket(sock, server_hostname="edge-mqtt.facebook.com")
    print(f"TLS: {ssl_sock.version()}")

    ssl_sock.sendall(packet)
    ssl_sock.settimeout(10)
    response = ssl_sock.recv(4096)
    print(f"Response: {len(response)} bytes: {response.hex()}")

    if len(response) >= 4:
        ptype = (response[0] >> 4) & 0x0F
        rc = response[3]
        meaning = {
            0: "Connection Accepted ✅",
            1: "Unacceptable Protocol Version",
            2: "Identifier Rejected",
            3: "Server Unavailable",
            4: "Bad Username or Password",
            5: "Not Authorized",
        }
        print(f"CONNACK: type={ptype}, rc={rc} ({meaning.get(rc, 'Unknown')})")

        if rc == 0:
            # Listen for messages
            print("\n🎉 CONNECTED! Listening for 15s...")
            ssl_sock.settimeout(2)
            end_time = time.time() + 15
            while time.time() < end_time:
                try:
                    data = ssl_sock.recv(4096)
                    if not data:
                        print("Connection closed")
                        break
                    pt = (data[0] >> 4) & 0x0F
                    print(f"\nPacket type={pt}, {len(data)}B")
                    if pt == 3:  # PUBLISH
                        parse_publish(data)
                    elif pt == 13:  # PINGREQ
                        ssl_sock.sendall(b"\xd0\x00")
                        print("  -> PINGRESP")
                except TimeoutError:
                    pass

        ssl_sock.close()
        return rc
    else:
        ssl_sock.close()
        print("Invalid response")
        return -1


def parse_publish(data: bytes) -> None:
    """Parse and print a PUBLISH packet."""
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

    topic_len = struct.unpack("!H", data[idx : idx + 2])[0]
    topic = data[idx + 2 : idx + 2 + topic_len].decode("utf-8", errors="replace")
    payload_start = idx + 2 + topic_len
    qos = (data[0] >> 1) & 0x03
    if qos > 0:
        payload_start += 2  # packet ID
    payload = data[payload_start:]

    print(f"  Topic: '{topic}'")
    try:
        decompressed = zlib.decompress(payload)
        text = decompressed.decode("utf-8", errors="replace")
        print(f"  Payload ({len(decompressed)}B): {text[:500]}")
    except Exception as e:
        print(f"  Raw ({len(payload)}B): {payload[:80].hex()}")
        print(f"  Decompress error: {e}")


if __name__ == "__main__":
    with open("/Users/bryantran/informatik/instagram-mcp/.instagram_session") as f:
        session = json.load(f)

    # Try bearer auth first (what mautrix uses)
    rc = try_connect(session, "bearer")

    if rc != 0:
        # Try sessionid auth (what TypeScript impl uses)
        rc = try_connect(session, "sessionid")

    if rc != 0:
        print("\n\n❌ Both auth modes failed.")
        print("Possible causes:")
        print("  1. Session expired (challenge_required seen earlier)")
        print("  2. Thrift encoding bug")
        print("  3. Missing required fields")
        print("\nLet's dump the Thrift bytes for inspection:")
        # Dump raw (uncompressed) thrift for inspection
        client_id = build_payload(session, "bearer")
        raw = zlib.decompress(client_id)
        print(f"Raw Thrift ({len(raw)} bytes):")
        print(raw.hex())
