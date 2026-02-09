"""MQTToT PoC v4: try different connect_flags and debug."""

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

    def _zigzag_i16(self, n):
        return (n << 1) ^ (n >> 15)

    def _zigzag_i32(self, n):
        return (n << 1) ^ (n >> 31)

    def _zigzag_i64(self, n):
        return (n << 1) ^ (n >> 63)

    def _write_field_header(self, field_id: int, thrift_type: int) -> None:
        delta = field_id - self._last_field_id
        if 0 < delta <= 15:
            self._buf.append((delta << 4) | thrift_type)
        else:
            self._buf.append(thrift_type)
            self._write_varint(self._zigzag_i16(field_id))
        self._last_field_id = field_id

    def write_bool(self, fid, v):
        self._write_field_header(fid, 1 if v else 2)

    def write_byte(self, fid, v):
        self._write_field_header(fid, 3)
        self._buf.append(v & 0xFF)

    def write_i32(self, fid, v):
        self._write_field_header(fid, 5)
        self._write_varint(self._zigzag_i32(v))

    def write_i64(self, fid, v):
        self._write_field_header(fid, 6)
        self._write_varint(self._zigzag_i64(v))

    def write_string(self, fid, v):
        data = v.encode("utf-8")
        self._write_field_header(fid, 8)
        self._write_varint(len(data))
        self._buf.extend(data)

    def write_list_i32(self, fid, values):
        self._write_field_header(fid, 9)
        if len(values) < 15:
            self._buf.append((len(values) << 4) | 5)
        else:
            self._buf.append(0xF0 | 5)
            self._write_varint(len(values))
        for v in values:
            self._write_varint(self._zigzag_i32(v))

    def write_map_str_str(self, fid, m):
        self._write_field_header(fid, 11)
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

    def write_struct_begin(self, fid):
        self._write_field_header(fid, 12)
        self._field_stack.append(self._last_field_id)
        self._last_field_id = 0

    def write_stop(self):
        self._buf.append(0)
        if self._field_stack:
            self._last_field_id = self._field_stack.pop()

    def getvalue(self):
        return bytes(self._buf)


def build_payload(session, auth_mode="bearer"):
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
    else:
        password = f"sessionid={session_id}"

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
    return zlib.compress(w.getvalue(), 9)


def send_connect(ssl_sock, client_id, keepalive=60, connect_flags=0xC2):
    protocol = b"MQTToT"
    var_header = struct.pack(
        f"!H{len(protocol)}sBBH",
        len(protocol),
        protocol,
        3,
        connect_flags,
        keepalive,
    )
    remaining_length = len(var_header) + len(client_id)
    packet = bytearray()
    packet.append(0x10)
    rl = remaining_length
    while True:
        byte = rl % 128
        rl //= 128
        if rl > 0:
            byte |= 0x80
        packet.append(byte)
        if rl == 0:
            break
    packet.extend(var_header)
    packet.extend(client_id)
    ssl_sock.sendall(bytes(packet))
    return len(packet)


def read_connack(ssl_sock):
    ssl_sock.settimeout(10)
    response = ssl_sock.recv(4096)
    if len(response) < 4:
        return -1, response
    rc = response[3]
    return rc, response


def make_ssl_sock():
    ctx = ssl.create_default_context(cafile=certifi.where())
    sock = socket.create_connection(("edge-mqtt.facebook.com", 443), timeout=10)
    return ctx.wrap_socket(sock, server_hostname="edge-mqtt.facebook.com")


if __name__ == "__main__":
    with open("/Users/bryantran/informatik/instagram-mcp/.instagram_session") as f:
        session = json.load(f)

    meanings = {
        0: "✅ Accepted",
        1: "Bad Protocol",
        2: "ID Rejected",
        3: "Server Unavailable",
        4: "Bad Auth",
        5: "Not Authorized",
    }

    # Test matrix: auth_mode × connect_flags
    tests = [
        ("bearer", 0xC2, "username+password+clean"),
        ("bearer", 0x02, "clean_session only"),
        ("sessionid", 0xC2, "username+password+clean"),
        ("sessionid", 0x02, "clean_session only"),
    ]

    for auth_mode, flags, desc in tests:
        print(f"\n--- {auth_mode} / flags={flags:#04x} ({desc}) ---")
        try:
            payload = build_payload(session, auth_mode)
            ssl_sock = make_ssl_sock()
            pkt_size = send_connect(ssl_sock, payload, connect_flags=flags)
            rc, raw = read_connack(ssl_sock)
            print(f"  Packet: {pkt_size}B | CONNACK rc={rc} ({meanings.get(rc, '?')})")
            if rc == 0:
                print("  🎉 SUCCESS!")
                # Listen briefly
                ssl_sock.settimeout(3)
                try:
                    data = ssl_sock.recv(4096)
                    pt = (data[0] >> 4) & 0x0F
                    print(f"  First message: type={pt}, {len(data)}B")
                except TimeoutError:
                    print("  (no messages in 3s)")
            ssl_sock.close()
        except Exception as e:
            print(f"  ERROR: {e}")
