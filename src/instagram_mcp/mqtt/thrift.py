"""Thrift Compact Protocol encoder for MQTToT CONNECT payloads.

Implements the subset of Thrift Compact Protocol needed to build
Instagram's MQTT CONNECT client_id payload. Based on the working
poc at scratch/mqtt_poc/mqtt_listen.py.
"""

from __future__ import annotations

import json
import time
import zlib

from instagram_mcp.mqtt.topics import SUBSCRIBE_TOPICS


class ThriftCompactWriter:
    """Thrift Compact Protocol binary encoder.

    Supports the field types needed for Instagram's RealtimeConfig:
    bool, byte, i32, i64, string, list<i32>, map<string,string>, struct.
    """

    def __init__(self) -> None:
        self._buf = bytearray()
        self._last_field_id = 0
        self._field_stack: list[int] = []

    def _write_varint(self, value: int) -> None:
        if value < 0:
            value = value & 0xFFFFFFFFFFFFFFFF
        while True:
            byte = value & 0x7F
            value >>= 7
            self._buf.append(byte | 0x80 if value else byte)
            if not value:
                break

    @staticmethod
    def _zigzag16(n: int) -> int:
        return (n << 1) ^ (n >> 15)

    @staticmethod
    def _zigzag32(n: int) -> int:
        return (n << 1) ^ (n >> 31)

    @staticmethod
    def _zigzag64(n: int) -> int:
        return (n << 1) ^ (n >> 63)

    def _write_field_header(self, field_id: int, type_id: int) -> None:
        delta = field_id - self._last_field_id
        if 0 < delta <= 15:
            self._buf.append((delta << 4) | type_id)
        else:
            self._buf.append(type_id)
            self._write_varint(self._zigzag16(field_id))
        self._last_field_id = field_id

    def write_bool(self, field_id: int, value: bool) -> None:
        """Write a boolean field (type 1=true, 2=false)."""
        self._write_field_header(field_id, 1 if value else 2)

    def write_byte(self, field_id: int, value: int) -> None:
        """Write a byte/i8 field (type 3)."""
        self._write_field_header(field_id, 3)
        self._buf.append(value & 0xFF)

    def write_i32(self, field_id: int, value: int) -> None:
        """Write an i32 field (type 5, zigzag encoded)."""
        self._write_field_header(field_id, 5)
        self._write_varint(self._zigzag32(value))

    def write_i64(self, field_id: int, value: int) -> None:
        """Write an i64 field (type 6, zigzag encoded)."""
        self._write_field_header(field_id, 6)
        self._write_varint(self._zigzag64(value))

    def write_string(self, field_id: int, value: str) -> None:
        """Write a string/binary field (type 8, length-prefixed UTF-8)."""
        data = value.encode("utf-8")
        self._write_field_header(field_id, 8)
        self._write_varint(len(data))
        self._buf.extend(data)

    def write_list_i32(self, field_id: int, values: list[int]) -> None:
        """Write a list<i32> field (type 9)."""
        self._write_field_header(field_id, 9)
        if len(values) < 15:
            self._buf.append((len(values) << 4) | 5)
        else:
            self._buf.append(0xF0 | 5)
            self._write_varint(len(values))
        for v in values:
            self._write_varint(self._zigzag32(v))

    def write_map_str_str(self, field_id: int, mapping: dict[str, str]) -> None:
        """Write a map<string,string> field (type 11)."""
        self._write_field_header(field_id, 11)
        if not mapping:
            self._buf.append(0)
            return
        self._write_varint(len(mapping))
        self._buf.append(0x88)  # key_type=string(8), value_type=string(8)
        for k, v in mapping.items():
            kb = k.encode("utf-8")
            self._write_varint(len(kb))
            self._buf.extend(kb)
            vb = v.encode("utf-8")
            self._write_varint(len(vb))
            self._buf.extend(vb)

    def write_struct_begin(self, field_id: int) -> None:
        """Begin a nested struct field (type 12). Must call write_stop() to close."""
        self._write_field_header(field_id, 12)
        self._field_stack.append(self._last_field_id)
        self._last_field_id = 0

    def write_stop(self) -> None:
        """End the current struct. Restores parent field context."""
        self._buf.append(0)
        if self._field_stack:
            self._last_field_id = self._field_stack.pop()

    def getvalue(self) -> bytes:
        """Return the encoded bytes."""
        return bytes(self._buf)


def build_connect_payload(session: dict[str, object]) -> bytes:
    """Build the zlib-compressed Thrift CONNECT payload from an instagrapi session.

    Args:
        session: Loaded instagrapi session dict (from .instagram_session file).

    Returns:
        Zlib-compressed Thrift-encoded RealtimeConfig bytes.
    """
    auth = session["authorization_data"]
    uuids = session["uuids"]
    dev = session["device_settings"]
    ua = session["user_agent"]

    uid = int(auth["ds_user_id"])
    phone_id = uuids["phone_id"]

    # Build bearer token from authorization_data
    token_json = json.dumps(auth, separators=(",", ":"))
    import base64

    password = f"authorization=Bearer IGT:2:{base64.b64encode(token_json.encode()).decode()}"

    w = ThriftCompactWriter()
    # Field 1: client_identifier (truncated phone_id)
    w.write_string(1, phone_id[:20])

    # Field 4: client_info (nested RealtimeClientInfo struct)
    w.write_struct_begin(4)
    w.write_i64(1, uid)
    w.write_string(2, ua)
    w.write_i64(3, 183)  # client_capabilities
    w.write_i64(4, 0)  # endpoint_capabilities
    w.write_i32(5, 1)  # publish_format
    w.write_bool(6, True)  # no_automatic_foreground
    w.write_bool(7, False)  # make_user_available_in_foreground
    w.write_string(8, phone_id)  # device_id
    w.write_bool(9, False)  # is_initially_foreground
    w.write_i32(10, 1)  # network_type
    w.write_i32(11, -1)  # network_subtype
    w.write_i64(12, int(time.time() * 1000) & 0xFFFFFFFF)  # client_mqtt_session_id
    w.write_list_i32(14, SUBSCRIBE_TOPICS)
    w.write_string(15, "cookie_auth")  # client_type
    w.write_i64(16, 567067343352427)  # app_id (Instagram Android)
    w.write_string(20, "")  # another_unknown
    w.write_byte(21, 3)  # client_stack
    w.write_stop()

    # Field 5: password (bearer token)
    w.write_string(5, password)

    # Field 8: app_specific_info (map<string,string>)
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
