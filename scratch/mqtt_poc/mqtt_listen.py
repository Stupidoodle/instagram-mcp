"""MQTToT listener: connect and print all incoming messages."""

import base64
import json
import socket
import ssl
import struct
import time
import zlib

import certifi


class ThriftWriter:
    def __init__(self):
        self._buf = bytearray()
        self._last_field_id = 0
        self._field_stack: list[int] = []

    def _write_varint(self, value):
        if value < 0:
            value = value & 0xFFFFFFFFFFFFFFFF
        while True:
            byte = value & 0x7F
            value >>= 7
            self._buf.append(byte | 0x80 if value else byte)
            if not value:
                break

    def _zz16(self, n):
        return (n << 1) ^ (n >> 15)

    def _zz32(self, n):
        return (n << 1) ^ (n >> 31)

    def _zz64(self, n):
        return (n << 1) ^ (n >> 63)

    def _fh(self, fid, tt):
        d = fid - self._last_field_id
        self._buf.append((d << 4) | tt) if 0 < d <= 15 else (
            self._buf.append(tt),
            self._write_varint(self._zz16(fid)),
        )
        self._last_field_id = fid

    def write_bool(self, f, v):
        self._fh(f, 1 if v else 2)

    def write_byte(self, f, v):
        self._fh(f, 3)
        self._buf.append(v & 0xFF)

    def write_i32(self, f, v):
        self._fh(f, 5)
        self._write_varint(self._zz32(v))

    def write_i64(self, f, v):
        self._fh(f, 6)
        self._write_varint(self._zz64(v))

    def write_string(self, f, v):
        d = v.encode("utf-8")
        self._fh(f, 8)
        self._write_varint(len(d))
        self._buf.extend(d)

    def write_list_i32(self, f, vals):
        self._fh(f, 9)
        self._buf.append((len(vals) << 4) | 5) if len(vals) < 15 else (
            self._buf.append(0xF0 | 5),
            self._write_varint(len(vals)),
        )
        for v in vals:
            self._write_varint(self._zz32(v))

    def write_map_str_str(self, f, m):
        self._fh(f, 11)
        if not m:
            self._buf.append(0)
            return
        self._write_varint(len(m))
        self._buf.append(0x88)
        for k, v in m.items():
            kb = k.encode("utf-8")
            self._write_varint(len(kb))
            self._buf.extend(kb)
            vb = v.encode("utf-8")
            self._write_varint(len(vb))
            self._buf.extend(vb)

    def write_struct_begin(self, f):
        self._fh(f, 12)
        self._field_stack.append(self._last_field_id)
        self._last_field_id = 0

    def write_stop(self):
        self._buf.append(0)
        if self._field_stack:
            self._last_field_id = self._field_stack.pop()

    def getvalue(self):
        return bytes(self._buf)


TOPIC_NAMES = {
    "34": "/pp",
    "88": "/pubsub",
    "132": "/ig_send_message",
    "133": "/ig_send_message_response",
    "134": "/ig_sub_iris",
    "135": "/ig_sub_iris_response",
    "146": "/ig_message_sync",
    "149": "/ig_realtime_sub",
    "150": "/t_region_hint",
    "179": "/ls_resp",
    "244": "/rs_req",
    "245": "/rs_resp",
    "274": "/t_rtc_log",
}


def build_payload(session):
    auth = session["authorization_data"]
    uuids = session["uuids"]
    dev = session["device_settings"]
    ua = session["user_agent"]
    uid = int(auth["ds_user_id"])
    pid = uuids["phone_id"]
    tok = json.dumps(auth, separators=(",", ":"))
    pw = f"authorization=Bearer IGT:2:{base64.b64encode(tok.encode()).decode()}"

    w = ThriftWriter()
    w.write_string(1, pid[:20])
    w.write_struct_begin(4)
    w.write_i64(1, uid)
    w.write_string(2, ua)
    w.write_i64(3, 183)
    w.write_i64(4, 0)
    w.write_i32(5, 1)
    w.write_bool(6, True)
    w.write_bool(7, False)
    w.write_string(8, pid)
    w.write_bool(9, False)
    w.write_i32(10, 1)
    w.write_i32(11, -1)
    w.write_i64(12, int(time.time() * 1000) & 0xFFFFFFFF)
    w.write_list_i32(14, [88, 135, 244, 149, 150, 245, 274, 133, 146, 179, 34])
    w.write_string(15, "cookie_auth")
    w.write_i64(16, 567067343352427)
    w.write_string(20, "")
    w.write_byte(21, 3)
    w.write_stop()
    w.write_string(5, pw)
    w.write_map_str_str(
        8,
        {
            "capabilities": "3brTvx0=",
            "app_version": dev["app_version"],
            "User-Agent": ua,
            "Accept-Language": "en-US",
            "platform": "android",
            "ig_mqtt_route": "django",
            "pubsub_msg_type_blacklist": "direct, typing_type",
            "auth_cache_enabled": "1",
        },
    )
    w.write_stop()
    return zlib.compress(w.getvalue(), 9)


def send_connect(sock, client_id, keepalive=60):
    proto = b"MQTToT"
    vh = struct.pack(f"!H{len(proto)}sBBH", len(proto), proto, 3, 0xC2, keepalive)
    rl = len(vh) + len(client_id)
    pkt = bytearray([0x10])
    while True:
        b = rl % 128
        rl //= 128
        pkt.append(b | 0x80 if rl > 0 else b)
        if rl == 0:
            break
    pkt.extend(vh)
    pkt.extend(client_id)
    sock.sendall(bytes(pkt))


def send_publish(sock, topic_id, payload_dict, qos=1, packet_id=1):
    """Send a PUBLISH packet with zlib-compressed JSON payload."""
    topic = str(topic_id).encode("utf-8")
    payload = zlib.compress(json.dumps(payload_dict).encode("utf-8"), 9)

    remaining = 2 + len(topic) + len(payload)
    if qos > 0:
        remaining += 2  # packet ID

    flags = 0x30 | ((qos & 0x03) << 1)  # PUBLISH + QoS
    pkt = bytearray([flags])
    rl = remaining
    while True:
        b = rl % 128
        rl //= 128
        pkt.append(b | 0x80 if rl > 0 else b)
        if rl == 0:
            break
    pkt.extend(struct.pack("!H", len(topic)))
    pkt.extend(topic)
    if qos > 0:
        pkt.extend(struct.pack("!H", packet_id))
    pkt.extend(payload)
    sock.sendall(bytes(pkt))
    print(f"  -> Published to topic {topic_id} ({len(payload)}B)")


def read_packet(sock) -> tuple[int, int, bytes] | None:
    """Read one MQTT packet. Returns (packet_type, first_byte, body) or None on timeout."""
    try:
        header = sock.recv(1)
        if not header:
            return None
        first_byte = header[0]
        ptype = (first_byte >> 4) & 0x0F

        # Read remaining length
        remaining = 0
        multiplier = 1
        while True:
            byte_data = sock.recv(1)
            if not byte_data:
                return None
            remaining += (byte_data[0] & 0x7F) * multiplier
            multiplier *= 128
            if not (byte_data[0] & 0x80):
                break

        # Read the rest
        body = bytearray()
        while len(body) < remaining:
            chunk = sock.recv(remaining - len(body))
            if not chunk:
                break
            body.extend(chunk)

        return ptype, first_byte, bytes(body)
    except TimeoutError:
        return None


def parse_publish(first_byte: int, body: bytes):
    """Parse a PUBLISH packet body and print contents."""
    idx = 0
    topic_len = struct.unpack("!H", body[idx : idx + 2])[0]
    topic = body[idx + 2 : idx + 2 + topic_len].decode("utf-8", errors="replace")
    payload_start = idx + 2 + topic_len

    qos = (first_byte >> 1) & 0x03
    packet_id = None
    if qos > 0:
        packet_id = struct.unpack("!H", body[payload_start : payload_start + 2])[0]
        payload_start += 2

    payload = body[payload_start:]
    topic_name = TOPIC_NAMES.get(topic, topic)

    print(f"\n{'=' * 60}")
    print(f"📩 PUBLISH on {topic_name} (topic={topic}, qos={qos}, pid={packet_id})")

    try:
        decompressed = zlib.decompress(payload)
        text = decompressed.decode("utf-8", errors="replace")
        try:
            parsed = json.loads(text)
            print(json.dumps(parsed, indent=2)[:1000])
        except json.JSONDecodeError:
            print(f"  Text ({len(decompressed)}B): {text[:500]}")
    except Exception:
        print(f"  Raw ({len(payload)}B): {payload[:100].hex()}")

    # Send PUBACK for QoS 1
    if qos == 1 and packet_id is not None:
        return packet_id
    return None


if __name__ == "__main__":
    with open("/Users/bryantran/informatik/instagram-mcp/.instagram_session") as f:
        session = json.load(f)

    print("=== Connecting to Instagram MQTT ===")
    payload = build_payload(session)
    ctx = ssl.create_default_context(cafile=certifi.where())
    sock = socket.create_connection(("edge-mqtt.facebook.com", 443), timeout=10)
    ssl_sock = ctx.wrap_socket(sock, server_hostname="edge-mqtt.facebook.com")
    print(f"TLS: {ssl_sock.version()}")

    send_connect(ssl_sock, payload)
    ssl_sock.settimeout(5)

    # Read CONNACK
    result = read_packet(ssl_sock)
    if result is None or result[0] != 2:
        print("Failed to get CONNACK")
        exit(1)

    ptype, first_byte, body = result
    rc = body[1] if len(body) >= 2 else -1
    if rc != 0:
        print(f"CONNACK rejected: rc={rc}")
        exit(1)

    print("✅ Connected!")

    # Get current seq_id from REST API
    print("\n=== Fetching seq_id from REST API ===")
    from instagrapi import Client as IgClient

    ig = IgClient()
    ig.load_settings("/Users/bryantran/informatik/instagram-mcp/.instagram_session")
    inbox = ig.private_request("direct_v2/inbox/", params={"limit": 1})
    seq_id = inbox.get("seq_id", 0)
    snapshot_at_ms = inbox.get("snapshot_at_ms", int(time.time() * 1000))
    print(f"  seq_id={seq_id}, snapshot_at_ms={snapshot_at_ms}")

    # Subscribe to Iris (DM sync) - topic 134
    print("\n=== Subscribing to Iris (DM sync) ===")
    send_publish(
        ssl_sock,
        134,
        {
            "seq_id": seq_id,
            "snapshot_at_ms": snapshot_at_ms,
            "snapshot_app_version": session["device_settings"]["app_version"],
            "subscription_type": "message",
        },
        qos=1,
        packet_id=1,
    )

    # Listen for messages
    print("\n=== Listening (60 seconds — send a DM to test!) ===")
    ssl_sock.settimeout(2)
    end_time = time.time() + 60
    msg_count = 0

    while time.time() < end_time:
        result = read_packet(ssl_sock)
        if result is None:
            continue

        ptype, first_byte, body = result
        if ptype == 3:  # PUBLISH
            pid = parse_publish(first_byte, body)
            if pid is not None:
                # Send PUBACK
                ssl_sock.sendall(struct.pack("!BBH", 0x40, 0x02, pid))
            msg_count += 1
        elif ptype == 13:  # PINGREQ
            ssl_sock.sendall(b"\xd0\x00")
            print("  (pingreq -> pingresp)")
        elif ptype == 4:  # PUBACK
            pass  # acknowledgment of our publish
        else:
            print(f"  Packet type={ptype}, {len(body)}B")

    print(f"\n=== Done. Received {msg_count} messages in 60s ===")
    ssl_sock.close()
