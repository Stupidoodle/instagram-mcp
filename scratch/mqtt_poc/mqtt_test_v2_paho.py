"""MQTToT PoC v2: using paho-mqtt with custom CONNECT override."""

import json
import struct
import threading
import time
import zlib

import certifi
import paho.mqtt.client as pmc

# ── Thrift Compact Protocol encoder ──────────────────────────────────────────


class ThriftWriter:
    def __init__(self):
        self._buf = bytearray()
        self._last_field_id = 0
        self._field_stack: list[int] = []

    def _write_varint(self, value: int) -> None:
        # Ensure unsigned
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
        self._write_field_header(field_id, 1 if value else 2)

    def write_byte(self, field_id: int, value: int) -> None:
        self._write_field_header(field_id, 3)
        self._buf.append(value & 0xFF)

    def write_i32(self, field_id: int, value: int) -> None:
        self._write_field_header(field_id, 5)
        self._write_varint(self._zigzag(value, 32))

    def write_i64(self, field_id: int, value: int) -> None:
        self._write_field_header(field_id, 6)
        self._write_varint(self._zigzag(value, 64))

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
            self._write_varint(self._zigzag(v, 32))

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


# ── MQTToT Client (paho subclass) ───────────────────────────────────────────


class MQTToTClient(pmc.Client):
    """paho MQTT client with MQTToT CONNECT packet."""

    def _send_connect(self, keepalive: int):
        protocol = b"MQTToT"
        proto_ver = 3
        connect_flags = 0xC2  # username + password + clean_session

        remaining_length = 2 + len(protocol) + 1 + 1 + 2 + len(self._client_id)

        packet = bytearray()
        packet.append(pmc.CONNECT)
        self._pack_remaining_length(packet, remaining_length)
        packet.extend(
            struct.pack(
                f"!H{len(protocol)}sBBH",
                len(protocol),
                protocol,
                proto_ver,
                connect_flags,
                keepalive,
            )
        )
        # Client ID WITHOUT length prefix (MQTToT deviation from standard MQTT)
        packet.extend(self._client_id)

        self._easy_log(pmc.MQTT_LOG_DEBUG, "Sending MQTToT CONNECT")
        return self._packet_queue(pmc.CONNECT, packet, 0, 0)


# ── Build Thrift payload ─────────────────────────────────────────────────────


def build_connect_payload(session: dict) -> bytes:
    auth_data = session["authorization_data"]
    uuids = session["uuids"]
    device = session["device_settings"]
    user_agent = session["user_agent"]
    user_id = int(auth_data["ds_user_id"])
    phone_id = uuids["phone_id"]

    # Use the same bearer token format as instagrapi
    import base64

    token_json = json.dumps(auth_data, separators=(",", ":"))
    bearer = f"Bearer IGT:2:{base64.b64encode(token_json.encode()).decode()}"
    password = f"authorization={bearer}"

    subscribe_topics = [88, 135, 149, 150, 133, 146, 34]

    w = ThriftWriter()
    # Outer struct: RealtimeConfig
    w.write_string(1, phone_id[:20])  # client_identifier

    # Field 4: client_info (nested struct)
    w.write_struct_begin(4)
    w.write_i64(1, user_id)
    w.write_string(2, user_agent)
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
    w.write_list_i32(14, subscribe_topics)
    w.write_string(15, "cookie_auth")
    w.write_i64(16, 567067343352427)  # app_id
    w.write_string(20, "")  # device_secret
    w.write_byte(21, 3)  # client_stack
    w.write_stop()  # end client_info

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

    w.write_stop()  # end outer struct

    payload = w.getvalue()
    compressed = zlib.compress(payload, 9)
    print(f"Thrift: {len(payload)}B -> zlib: {len(compressed)}B")
    return compressed


# ── Callbacks ────────────────────────────────────────────────────────────────


def on_connect(client, userdata, flags, rc, properties=None):
    meaning = {
        0: "Connection Accepted",
        1: "Unacceptable Protocol Version",
        2: "Identifier Rejected",
        3: "Server Unavailable",
        4: "Bad Username or Password",
        5: "Not Authorized",
    }
    print(f"CONNACK: rc={rc} ({meaning.get(rc, 'Unknown')})")
    if rc == 0:
        print("✅ CONNECTED!")
        userdata["connected"].set()


def on_message(client, userdata, msg):
    topic = msg.topic
    try:
        payload = zlib.decompress(msg.payload)
        text = payload.decode("utf-8", errors="replace")
        print(f"\n📩 Message on topic '{topic}' ({len(payload)}B):")
        print(f"   {text[:500]}")
    except Exception:
        print(f"\n📩 Raw message on topic '{topic}' ({len(msg.payload)}B)")
        print(f"   {msg.payload[:100].hex()}")


def on_disconnect(client, userdata, rc, properties=None):
    print(f"Disconnected: rc={rc}")


def on_log(client, userdata, level, buf):
    if level <= pmc.MQTT_LOG_WARNING:
        print(f"  [paho] {buf}")


# ── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    with open("/Users/bryantran/informatik/instagram-mcp/.instagram_session") as f:
        session = json.load(f)

    print("=== Building payload ===")
    client_id = build_connect_payload(session)

    print("\n=== Creating MQTToT client ===")
    userdata = {"connected": threading.Event()}
    client = MQTToTClient(
        callback_api_version=pmc.CallbackAPIVersion.VERSION2,
        client_id=client_id,
        clean_session=True,
        protocol=pmc.MQTTv31,
        transport="tcp",
        userdata=userdata,
    )
    client.on_connect = on_connect
    client.on_message = on_message
    client.on_disconnect = on_disconnect
    client.on_log = on_log
    client.tls_set(ca_certs=certifi.where())

    print("\n=== Connecting to edge-mqtt.facebook.com:443 ===")
    client.connect("edge-mqtt.facebook.com", 443, keepalive=60)
    client.loop_start()

    # Wait for CONNACK
    if userdata["connected"].wait(timeout=10):
        print("\n=== Listening for 10 seconds ===")
        time.sleep(10)
    else:
        print("\n❌ Connection timeout or rejected")

    client.loop_stop()
    client.disconnect()
    print("\nDone.")
