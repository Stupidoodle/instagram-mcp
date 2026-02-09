# Mautrix-Instagram: Ready-to-Port Code Snippets

**Purpose**: Copy-paste ready code snippets from mautrix-instagram for porting to instagram-mcp

---

## 1. Thrift Type Definitions

```python
# From: mauigpapi/mqtt/thrift/type.py
from enum import IntEnum

class TType(IntEnum):
    STOP = 0
    TRUE = 1
    FALSE = 2
    BYTE = 3
    I16 = 4
    I32 = 5
    I64 = 6
    # DOUBLE = 7
    BINARY = 8
    STRING = 8
    LIST = 9
    SET = 10
    MAP = 11
    STRUCT = 12

    # internal
    BOOL = 0xA1
```

---

## 2. Thrift Writer (Complete Implementation)

```python
# From: mauigpapi/mqtt/thrift/write.py
from __future__ import annotations

from typing import Any
import io

from .type import TType


class ThriftWriter(io.BytesIO):
    prev_field_id: int
    stack: list[int]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.prev_field_id = 0
        self.stack = []

    def _push_stack(self) -> None:
        self.stack.append(self.prev_field_id)
        self.prev_field_id = 0

    def _pop_stack(self) -> None:
        if self.stack:
            self.prev_field_id = self.stack.pop()

    def _write_byte(self, byte: int | TType) -> None:
        self.write(bytes([byte]))

    @staticmethod
    def _to_zigzag(val: int, bits: int) -> int:
        return (val << 1) ^ (val >> (bits - 1))

    def _write_varint(self, val: int) -> None:
        while True:
            byte = val & ~0x7F
            if byte == 0:
                self._write_byte(val)
                break
            elif byte == -128:
                self._write_byte(0)
                break
            else:
                self._write_byte((val & 0xFF) | 0x80)
                val = val >> 7

    def _write_word(self, val: int) -> None:
        self._write_varint(self._to_zigzag(val, 16))

    def _write_int(self, val: int) -> None:
        self._write_varint(self._to_zigzag(val, 32))

    def _write_long(self, val: int) -> None:
        self._write_varint(self._to_zigzag(val, 64))

    def write_field_begin(self, field_id: int, ttype: TType) -> None:
        ttype_val = ttype.value
        delta = field_id - self.prev_field_id
        if 0 < delta < 16:
            self._write_byte((delta << 4) | ttype_val)
        else:
            self._write_byte(ttype_val)
            self._write_word(field_id)
        self.prev_field_id = field_id

    def write_map(
        self, field_id: int, key_type: TType, value_type: TType, val: dict[Any, Any]
    ) -> None:
        self.write_field_begin(field_id, TType.MAP)
        if not map:
            self._write_byte(0)
            return
        self._write_varint(len(val))
        self._write_byte(((key_type.value & 0xF) << 4) | (value_type.value & 0xF))
        for key, value in val.items():
            self.write_val(None, key_type, key)
            self.write_val(None, value_type, value)

    def write_string_direct(self, val: str | bytes) -> None:
        if isinstance(val, str):
            val = val.encode("utf-8")
        self._write_varint(len(val))
        self.write(val)

    def write_stop(self) -> None:
        self._write_byte(TType.STOP.value)
        self._pop_stack()

    def write_int8(self, field_id: int, val: int) -> None:
        self.write_field_begin(field_id, TType.BYTE)
        self._write_byte(val)

    def write_int16(self, field_id: int, val: int) -> None:
        self.write_field_begin(field_id, TType.I16)
        self._write_word(val)

    def write_int32(self, field_id: int, val: int) -> None:
        self.write_field_begin(field_id, TType.I32)
        self._write_int(val)

    def write_int64(self, field_id: int, val: int) -> None:
        self.write_field_begin(field_id, TType.I64)
        self._write_long(val)

    def write_list(self, field_id: int, item_type: TType, val: list[Any]) -> None:
        self.write_field_begin(field_id, TType.LIST)
        if len(val) < 0x0F:
            self._write_byte((len(val) << 4) | item_type.value)
        else:
            self._write_byte(0xF0 | item_type.value)
            self._write_varint(len(val))
        for item in val:
            self.write_val(None, item_type, item)

    def write_struct_begin(self, field_id: int) -> None:
        self.write_field_begin(field_id, TType.STRUCT)
        self._push_stack()

    def write_val(self, field_id: int | None, ttype: TType, val: Any) -> None:
        if ttype == TType.BOOL:
            if field_id is None:
                raise ValueError("booleans can only be in structs")
            self.write_field_begin(field_id, TType.TRUE if val else TType.FALSE)
            return
        if field_id is not None:
            self.write_field_begin(field_id, ttype)
        if ttype == TType.BYTE:
            self._write_byte(val)
        elif ttype == TType.I16:
            self._write_word(val)
        elif ttype == TType.I32:
            self._write_int(val)
        elif ttype == TType.I64:
            self._write_long(val)
        elif ttype == TType.BINARY:
            self.write_string_direct(val)
        else:
            raise ValueError(f"{ttype} is not supported by write_val()")

    def write_struct(self, obj: Any) -> None:
        for field_id in iter(obj.thrift_spec):
            field_type, field_name, inner_type = obj.thrift_spec[field_id]

            val = getattr(obj, field_name, None)
            if val is None:
                continue

            if field_type in (
                TType.BOOL,
                TType.BYTE,
                TType.I16,
                TType.I32,
                TType.I64,
                TType.BINARY,
            ):
                self.write_val(field_id, field_type, val)
            elif field_type in (TType.LIST, TType.SET):
                self.write_list(field_id, inner_type, val)
            elif field_type == TType.MAP:
                (key_type, _), (value_type, _) = inner_type
                self.write_map(field_id, key_type, value_type, val)
            elif field_type == TType.STRUCT:
                self.write_struct_begin(field_id)
                self.write_struct(val)
        self.write_stop()
```

---

## 3. Thrift Reader (Complete Implementation)

```python
# From: mauigpapi/mqtt/thrift/read.py
from __future__ import annotations

import io

from .type import TType


class ThriftReader(io.BytesIO):
    prev_field_id: int
    stack: list[int]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.prev_field_id = 0
        self.stack = []

    def _push_stack(self) -> None:
        self.stack.append(self.prev_field_id)
        self.prev_field_id = 0

    def _pop_stack(self) -> None:
        if self.stack:
            self.prev_field_id = self.stack.pop()

    def _read_byte(self, signed: bool = False) -> int:
        return int.from_bytes(self.read(1), "big", signed=signed)

    @staticmethod
    def _from_zigzag(val: int) -> int:
        return (val >> 1) ^ -(val & 1)

    def read_small_int(self) -> int:
        return self._from_zigzag(self.read_varint())

    def read_varint(self) -> int:
        shift = 0
        result = 0
        while True:
            byte = self._read_byte()
            result |= (byte & 0x7F) << shift
            if (byte & 0x80) == 0:
                break
            shift += 7
        return result

    def read_field(self) -> TType:
        byte = self._read_byte()
        if byte == 0:
            return TType.STOP
        delta = (byte & 0xF0) >> 4
        if delta == 0:
            self.prev_field_id = self._from_zigzag(self.read_varint())
        else:
            self.prev_field_id += delta
        return TType(byte & 0x0F)
```

---

## 4. MQTToT Client (Custom MQTT CONNECT)

```python
# From: mauigpapi/mqtt/otclient.py
import struct
import paho.mqtt.client


class MQTToTClient(paho.mqtt.client.Client):
    # This is equivalent to the original _send_connect, except:
    # * the protocol ID is MQTToT.
    # * the client ID is sent without a length.
    # * all extra stuff like wills, usernames, passwords and MQTTv5 is removed.
    def _send_connect(self, keepalive):
        proto_ver = self._protocol
        protocol = b"MQTToT"

        remaining_length = 2 + len(protocol) + 1 + 1 + 2 + len(self._client_id)

        # Username, password, clean session
        connect_flags = 0x80 + 0x40 + 0x02

        command = paho.mqtt.client.CONNECT
        packet = bytearray()
        packet.append(command)

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
        packet.extend(self._client_id)

        self._keepalive = keepalive
        self._easy_log(
            paho.mqtt.client.MQTT_LOG_DEBUG,
            "Sending CONNECT",
        )
        return self._packet_queue(command, packet, 0, 0)
```

---

## 5. Topic Definitions

```python
# From: mauigpapi/mqtt/subscription.py
from enum import Enum

_topic_map: dict[str, str] = {
    "/pp": "34",  # unknown
    "/ig_sub_iris": "134",
    "/ig_sub_iris_response": "135",
    "/ig_message_sync": "146",
    "/ig_send_message": "132",
    "/ig_send_message_response": "133",
    "/ig_realtime_sub": "149",
    "/pubsub": "88",
    "/t_fs": "102",  # Foreground state
    "/graphql": "9",
    "/t_region_hint": "150",
    "/mqtt_health_stats": "/mqtt_health_stats",
    "/ls_resp": "179",
    "/rs_req": "244",
    "/rs_resp": "245",
    "/t_rtc_log": "274",
}

_reverse_topic_map: dict[str, str] = {value: key for key, value in _topic_map.items()}


class RealtimeTopic(Enum):
    SUB_IRIS = "/ig_sub_iris"
    SUB_IRIS_RESPONSE = "/ig_sub_iris_response"
    MESSAGE_SYNC = "/ig_message_sync"
    SEND_MESSAGE = "/ig_send_message"
    SEND_MESSAGE_RESPONSE = "/ig_send_message_response"
    REALTIME_SUB = "/ig_realtime_sub"
    PUBSUB = "/pubsub"
    FOREGROUND_STATE = "/t_fs"
    GRAPHQL = "/graphql"
    REGION_HINT = "/t_region_hint"
    MQTT_HEALTH_STATS = "/mqtt_health_stats"
    UNKNOWN_PP = "/pp"
    LIGHTSPEED_RESPONSE = "/ls_resp"
    RS_REQ = "/rs_req"
    RS_RESP = "/rs_resp"
    T_RTC_LOG = "/t_rtc_log"

    @property
    def encoded(self) -> str:
        return _topic_map[self.value]

    @staticmethod
    def decode(val: str) -> "RealtimeTopic":
        return RealtimeTopic(_reverse_topic_map[val])
```

---

## 6. Thrift Data Structures

```python
# From: mauigpapi/mqtt/thrift/ig_objects.py
# NOTE: This uses attrs and a custom @autospec decorator
# You'll need to adapt this to your dataclass system

from typing import Dict, List
from dataclasses import dataclass

# Simplified without autospec - you need to add thrift_spec manually
@dataclass
class RealtimeClientInfo:
    user_id: int
    user_agent: str
    client_capabilities: int
    endpoint_capabilities: int
    publish_format: int
    no_automatic_foreground: bool
    make_user_available_in_foreground: bool
    device_id: str
    is_initially_foreground: bool
    network_type: int
    network_subtype: int
    client_mqtt_session_id: int
    client_ip_address: str = None
    subscribe_topics: List[int] = None
    client_type: str = None
    app_id: int = None
    override_nectar_logging: bool = None
    connect_token_hash: str = None
    region_preference: str = None
    device_secret: str = None
    client_stack: int = None
    fbns_connection_key: int = None
    fbns_connection_secret: str = None
    fbns_device_id: str = None
    fbns_device_secret: str = None
    luid: int = None

    # You need to define thrift_spec like this:
    thrift_spec = {
        1: (TType.I64, "user_id", None),
        2: (TType.BINARY, "user_agent", None),
        3: (TType.I64, "client_capabilities", None),
        4: (TType.I64, "endpoint_capabilities", None),
        5: (TType.I32, "publish_format", None),
        6: (TType.BOOL, "no_automatic_foreground", None),
        7: (TType.BOOL, "make_user_available_in_foreground", None),
        8: (TType.BINARY, "device_id", None),
        9: (TType.BOOL, "is_initially_foreground", None),
        10: (TType.I32, "network_type", None),
        11: (TType.I32, "network_subtype", None),
        12: (TType.I64, "client_mqtt_session_id", None),
        13: (TType.BINARY, "client_ip_address", None),
        14: (TType.LIST, "subscribe_topics", TType.I32),
        15: (TType.BINARY, "client_type", None),
        16: (TType.I64, "app_id", None),
        17: (TType.BOOL, "override_nectar_logging", None),
        18: (TType.BINARY, "connect_token_hash", None),
        19: (TType.BINARY, "region_preference", None),
        20: (TType.BINARY, "device_secret", None),
        21: (TType.BYTE, "client_stack", None),
        22: (TType.I64, "fbns_connection_key", None),
        23: (TType.BINARY, "fbns_connection_secret", None),
        24: (TType.BINARY, "fbns_device_id", None),
        25: (TType.BINARY, "fbns_device_secret", None),
        26: (TType.I64, "luid", None),
    }


@dataclass
class RealtimeConfig:
    client_identifier: str
    will_topic: str = None
    will_message: str = None
    client_info: RealtimeClientInfo = None
    password: str = None
    get_diffs_request: List[str] = None
    zero_rating_token_hash: str = None
    app_specific_info: Dict[str, str] = None

    thrift_spec = {
        1: (TType.BINARY, "client_identifier", None),
        2: (TType.BINARY, "will_topic", None),
        3: (TType.BINARY, "will_message", None),
        4: (TType.STRUCT, "client_info", None),
        5: (TType.BINARY, "password", None),
        6: (TType.LIST, "get_diffs_request", TType.BINARY),
        7: (TType.BINARY, "zero_rating_token_hash", None),
        8: (TType.MAP, "app_specific_info", ((TType.BINARY, None), (TType.BINARY, None))),
    }

    def to_thrift(self) -> bytes:
        buf = ThriftWriter()
        buf.write_struct(self)
        return buf.getvalue()
```

---

## 7. Building Client ID

```python
# From: mauigpapi/mqtt/conn.py (lines 186-237)
import zlib
import time
import json

def _form_client_id(self, state) -> bytes:
    """
    Form the MQTT client ID for Instagram.

    Args:
        state: AndroidState object with session, device, user info

    Returns:
        Zlib-compressed Thrift-encoded RealtimeConfig
    """
    subscribe_topics = [
        88,   # PUBSUB
        135,  # SUB_IRIS_RESPONSE
        244,  # RS_REQ
        149,  # REALTIME_SUB
        150,  # REGION_HINT
        245,  # RS_RESP
        274,  # T_RTC_LOG
        133,  # SEND_MESSAGE_RESPONSE
        146,  # MESSAGE_SYNC (THE IMPORTANT ONE)
        179,  # LIGHTSPEED_RESPONSE
        34,   # UNKNOWN_PP
    ]

    password = f"authorization={state.session.authorization}"

    everclear_subscriptions = {
        "inapp_notification_subscribe_comment": "17899377895239777",
        "inapp_notification_subscribe_comment_mention_and_reply": "17899377895239777",
        "video_call_participant_state_delivery": "17977239895057311",
        "inapp_notification_subscribe_story_emoji_reaction": "17899377895239777",
        "inapp_notification_subscribe_prompt_sticker_reply": "17899377895239777",
        "inapp_notification_subscribe_fundraiser_cohost_invited": "17899377895239777",
        "inapp_notification_subscribe_watch_receipt": "17899377895239777",
    }

    cfg = RealtimeConfig(
        client_identifier=state.device.phone_id[:20],
        client_info=RealtimeClientInfo(
            user_id=int(state.user_id),
            user_agent=state.user_agent,
            client_capabilities=0b10110111,  # 183
            endpoint_capabilities=0,
            publish_format=1,
            no_automatic_foreground=True,
            make_user_available_in_foreground=False,
            device_id=state.device.phone_id,
            is_initially_foreground=False,
            network_type=1,
            network_subtype=-1,
            client_mqtt_session_id=int(time.time() * 1000) & 0xFFFFFFFF,
            subscribe_topics=subscribe_topics,
            client_type="cookie_auth",
            app_id=567067343352427,
            device_secret="",
            client_stack=3,
        ),
        password=password,
        app_specific_info={
            "capabilities": state.application.CAPABILITIES,
            "app_version": state.application.APP_VERSION,
            "everclear_subscriptions": json.dumps(everclear_subscriptions),
            "User-Agent": state.user_agent,
            "Accept-Language": state.device.language.replace("_", "-"),
            "platform": "android",
            "ig_mqtt_route": "django",
            "pubsub_msg_type_blacklist": "direct, typing_type",
            "auth_cache_enabled": "1",
        },
    )

    # Encode to Thrift, then compress with zlib level 9
    return zlib.compress(cfg.to_thrift(), level=9)
```

---

## 8. Message Handler

```python
# From: mauigpapi/mqtt/conn.py (lines 493-531)
import zlib
import json

def _on_message_handler(self, client, userdata, message):
    """
    Handle incoming MQTT message.

    Args:
        client: MQTT client
        userdata: User data (unused)
        message: paho.mqtt.client.MQTTMessage
    """
    try:
        # Decode topic from numeric string to enum
        topic = RealtimeTopic.decode(message.topic)

        # ALL Instagram MQTT messages are zlib-compressed!
        message.payload = zlib.decompress(message.payload)

        if topic == RealtimeTopic.MESSAGE_SYNC:
            self._on_message_sync(message.payload)
        elif topic == RealtimeTopic.PUBSUB:
            self._on_pubsub(message.payload)
        elif topic == RealtimeTopic.REALTIME_SUB:
            self._on_realtime_sub(message.payload)
        elif topic == RealtimeTopic.SEND_MESSAGE_RESPONSE:
            self._handle_send_response(message)
        elif topic == RealtimeTopic.UNKNOWN_PP:
            # Reconnection signal
            self.log.warning("Reconnecting after receiving /pp message")
            asyncio.create_task(self._reconnect())
        else:
            # Unknown topic
            self.log.debug(f"No handler for MQTT message in {topic.value}: {message.payload}")

    except Exception:
        self.log.exception("Error in incoming MQTT message handler")
        self.log.trace(f"Errored MQTT payload: {message.payload}")


def _on_message_sync(self, payload: bytes) -> None:
    """
    Handle /ig_message_sync (topic 146) messages.

    Args:
        payload: Decompressed JSON payload
    """
    # Parse JSON (payload is list of IrisPayload objects)
    parsed = json.loads(payload.decode("utf-8"))

    for sync_item in parsed:
        # Each item has: data[], message_type, seq_id, event
        seq_id = sync_item.get("seq_id")

        # Update sequence ID
        if seq_id and seq_id > self._iris_seq_id:
            self.log.debug(f"Got new seq_id: {seq_id}")
            self._iris_seq_id = seq_id
            self._iris_snapshot_at_ms = int(time.time() * 1000)
            # Save seq_id to persistent storage here!

        # Process each data item
        for part in sync_item.get("data", []):
            # part has: op, path, value
            op = part.get("op")  # "add", "replace", "remove"
            path = part.get("path")  # e.g., "/direct_v2/threads/123/items/456"
            value = part.get("value", "{}")  # JSON string

            if path.startswith("/direct_v2/threads/"):
                # Parse thread and item IDs from path
                parsed_path = self._parse_direct_thread_path(path)
                thread_id = parsed_path.get("thread_id")
                item_id = parsed_path.get("item_id")

                # Parse value JSON
                value_data = json.loads(value) if value != "{}" else {}

                # Handle message
                print(f"New message in thread {thread_id}: {value_data}")
```

---

## 9. Path Parsing

```python
# From: mauigpapi/mqtt/conn.py (lines 309-337)

def _parse_direct_thread_path(self, path: str) -> dict:
    """
    Parse Instagram direct thread path.

    Examples:
        /direct_v2/threads/340282366841710300949128194755493044420/items/28446322987695433556816519053594624
        /direct_v2/threads/{thread_id}/items/{item_id}/reactions/{type}/{user_id}
        /direct_v2/threads/{thread_id}/participants/{user_id}/has_seen
        /direct_v2/inbox/threads/{thread_id}

    Args:
        path: Instagram path string

    Returns:
        Dictionary with parsed components
    """
    try:
        blank, direct_v2, threads, thread_id, *rest = path.split("/")
    except (ValueError, IndexError) as e:
        raise ValueError(f"Invalid path format: {path}")

    if (blank, direct_v2, threads) != ("", "direct_v2", "threads"):
        raise ValueError(f"Unexpected path prefix: {path}")

    additional = {"thread_id": thread_id}

    if rest:
        subitem_key = rest[0]
        if subitem_key == "approval_required_for_new_members":
            additional["approval_required_for_new_members"] = True
        elif subitem_key == "thread_image":
            additional["is_thread_image"] = True
        elif subitem_key == "participants" and len(rest) > 2 and rest[2] == "has_seen":
            additional["has_seen"] = int(rest[1])
        elif subitem_key == "items":
            additional["item_id"] = rest[1]
            if len(rest) > 4 and rest[2] == "reactions":
                additional["reaction_type"] = rest[3]
                additional["reaction_user_id"] = int(rest[4])
        elif subitem_key in "admin_user_ids":
            additional["admin_user_id"] = int(rest[1])
        elif subitem_key == "activity_indicator_id":
            additional["activity_indicator_id"] = rest[1]

    return additional
```

---

## 10. Publishing Messages

```python
# From: mauigpapi/mqtt/conn.py (lines 684-698)
import zlib
import json

def publish(self, topic: RealtimeTopic, payload: str | bytes | dict) -> int:
    """
    Publish message to MQTT topic.

    Args:
        topic: RealtimeTopic enum
        payload: Message payload (dict, str, or bytes)

    Returns:
        Message ID for tracking PUBACK
    """
    # Convert to bytes
    if isinstance(payload, dict):
        payload = json.dumps(payload)
    if isinstance(payload, str):
        payload = payload.encode("utf-8")

    # Compress with zlib level 9
    payload = zlib.compress(payload, level=9)

    # Publish with QoS 1 (at least once delivery)
    result = self._client.publish(topic.encoded, payload, qos=1)

    return result.mid
```

---

## 11. Sending Text Messages

```python
# From: mauigpapi/mqtt/conn.py (lines 946-978)

def send_text(
    self,
    thread_id: str,
    text: str = "",
    client_context: str | None = None,
) -> int:
    """
    Send text message via MQTT.

    Args:
        thread_id: Instagram thread ID
        text: Message text
        client_context: Unique client context (generated if None)

    Returns:
        Message ID
    """
    client_context = client_context or self._gen_client_context()

    payload = {
        "thread_id": thread_id,
        "client_context": client_context,
        "offline_threading_id": client_context,
        "action": "send_item",
        "item_type": "text",
        "text": text,
        "is_shh_mode": "0",
    }

    return self.publish(RealtimeTopic.SEND_MESSAGE, payload)


def _gen_client_context(self) -> str:
    """Generate unique client context for message."""
    import random
    return str((int(time.time() * 1000) << 22) + random.randint(10000, 5000000))
```

---

## 12. Iris Subscribe (Resume from Sequence ID)

```python
# From: mauigpapi/mqtt/conn.py (lines 720-745)

async def iris_subscribe(self, seq_id: int, snapshot_at_ms: int) -> dict:
    """
    Subscribe to Iris message sync stream.

    Args:
        seq_id: Last received sequence ID (0 for first connection)
        snapshot_at_ms: Timestamp of last snapshot

    Returns:
        Response dict from server
    """
    payload = {
        "seq_id": seq_id,
        "snapshot_at_ms": snapshot_at_ms,
        "snapshot_app_version": self.app_version,
        "timezone_offset": self.timezone_offset,
        "subscription_type": "message",
    }

    # Publish to SUB_IRIS (134), expect response on SUB_IRIS_RESPONSE (135)
    self.publish(RealtimeTopic.SUB_IRIS, payload)

    # Wait for response (you need to implement waiting logic)
    # The response will come on topic 135
    # Response format: {"error_type": null, "error_message": null, "latest_seq_id": 123}
```

---

## 13. Asyncio Socket Integration

```python
# From: mauigpapi/mqtt/conn.py (lines 241-251)
import asyncio

def _on_socket_open(self, client, userdata, sock):
    """Register socket with asyncio event loop for reading."""
    loop = asyncio.get_event_loop()
    loop.add_reader(sock, client.loop_read)


def _on_socket_close(self, client, userdata, sock):
    """Unregister socket from asyncio event loop."""
    loop = asyncio.get_event_loop()
    loop.remove_reader(sock)


def _on_socket_register_write(self, client, userdata, sock):
    """Register socket with asyncio event loop for writing."""
    loop = asyncio.get_event_loop()
    loop.add_writer(sock, client.loop_write)


def _on_socket_unregister_write(self, client, userdata, sock):
    """Unregister socket from asyncio event loop for writing."""
    loop = asyncio.get_event_loop()
    loop.remove_writer(sock)
```

---

## 14. Main Listen Loop

```python
# From: mauigpapi/mqtt/conn.py (lines 577-655)
import asyncio
import paho.mqtt.client as pmc

async def listen(self, seq_id: int = None, snapshot_at_ms: int = None) -> None:
    """
    Main MQTT listen loop.

    Args:
        seq_id: Last received sequence ID (None for first connection)
        snapshot_at_ms: Timestamp of last snapshot
    """
    self._iris_seq_id = seq_id or 0
    self._iris_snapshot_at_ms = snapshot_at_ms or int(time.time() * 1000)

    # Connect
    self._client.connect_async("edge-mqtt.facebook.com", 443, keepalive=60)
    self._client.loop_start()  # Start network loop in background thread

    # Wait for connection
    while not self._client.is_connected():
        await asyncio.sleep(0.1)

    # Subscribe to iris
    await self.iris_subscribe(self._iris_seq_id, self._iris_snapshot_at_ms)

    # Main loop
    while True:
        try:
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            self._client.disconnect()
            self._client.loop_stop()
            return

        # Check connection status
        if not self._client.is_connected():
            raise Exception("MQTT disconnected")
```

---

## 15. Complete Minimal Example

```python
import asyncio
import logging
import paho.mqtt.client as pmc
import zlib
import json
import time

# Import all the classes above
# from .thrift import ThriftWriter, TType
# from .mqtt_client import MQTToTClient
# from .topics import RealtimeTopic
# from .structures import RealtimeConfig, RealtimeClientInfo

class InstagramMQTT:
    def __init__(self, state):
        self.state = state
        self.log = logging.getLogger(__name__)
        self._iris_seq_id = 0
        self._iris_snapshot_at_ms = int(time.time() * 1000)

        # Create client
        client_id = self._form_client_id(state)
        self._client = MQTToTClient(
            client_id=client_id,
            clean_session=True,
            protocol=pmc.MQTTv31,
        )

        # Setup callbacks
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message_handler

        # Setup TLS
        self._client.tls_set()

    def _form_client_id(self, state) -> bytes:
        # Use code from snippet #7
        pass

    def _on_connect(self, client, userdata, flags, rc):
        if rc != 0:
            self.log.error(f"Connection failed: {pmc.connack_string(rc)}")
            return

        self.log.info("Connected to Instagram MQTT")

        # Subscribe to iris
        asyncio.create_task(self.iris_subscribe(self._iris_seq_id, self._iris_snapshot_at_ms))

    def _on_message_handler(self, client, userdata, message):
        # Use code from snippet #8
        pass

    def _on_message_sync(self, payload: bytes):
        # Use code from snippet #8
        pass

    async def iris_subscribe(self, seq_id: int, snapshot_at_ms: int):
        # Use code from snippet #12
        pass

    def publish(self, topic, payload):
        # Use code from snippet #10
        pass

    def send_text(self, thread_id: str, text: str):
        # Use code from snippet #11
        pass

    async def listen(self):
        """Start listening for messages."""
        self._client.connect_async("edge-mqtt.facebook.com", 443, keepalive=60)
        self._client.loop_start()

        # Wait forever
        while True:
            await asyncio.sleep(1)
            if not self._client.is_connected():
                self.log.warning("Disconnected, reconnecting...")
                self._client.reconnect()


# Usage
async def main():
    # Create state object with session/device info
    state = ...  # Your AndroidState

    mqtt = InstagramMQTT(state)
    await mqtt.listen()

if __name__ == "__main__":
    asyncio.run(main())
```

---

## Key Constants

```python
# Instagram MQTT constants
MQTT_HOST = "edge-mqtt.facebook.com"
MQTT_PORT = 443
MQTT_KEEPALIVE = 60
MQTT_PROTOCOL_NAME = b"MQTToT"
MQTT_PROTOCOL_VERSION = 3  # MQTT 3.1

# App constants
INSTAGRAM_APP_ID = 567067343352427
CLIENT_CAPABILITIES = 0b10110111  # 183 in decimal

# Topic IDs
TOPIC_MESSAGE_SYNC = "146"
TOPIC_SEND_MESSAGE = "132"
TOPIC_SEND_MESSAGE_RESPONSE = "133"
TOPIC_SUB_IRIS = "134"
TOPIC_SUB_IRIS_RESPONSE = "135"

# Everclear subscriptions
EVERCLEAR_SUBSCRIPTIONS = {
    "inapp_notification_subscribe_comment": "17899377895239777",
    "inapp_notification_subscribe_comment_mention_and_reply": "17899377895239777",
    "video_call_participant_state_delivery": "17977239895057311",
    "inapp_notification_subscribe_story_emoji_reaction": "17899377895239777",
    "inapp_notification_subscribe_prompt_sticker_reply": "17899377895239777",
    "inapp_notification_subscribe_fundraiser_cohost_invited": "17899377895239777",
    "inapp_notification_subscribe_watch_receipt": "17899377895239777",
}
```

---

## Testing Snippets

```python
# Test Thrift encoding
def test_thrift_encoding():
    from .thrift import ThriftWriter, TType

    writer = ThriftWriter()
    writer.write_int32(1, 42)
    writer.write_string_direct("hello")
    writer.write_stop()

    result = writer.getvalue()
    print(f"Encoded: {result.hex()}")


# Test client ID formation
def test_client_id():
    state = ...  # Your state object
    client_id = _form_client_id(state)

    print(f"Client ID length: {len(client_id)} bytes")
    print(f"First 20 bytes: {client_id[:20].hex()}")

    # Decompress to verify
    decompressed = zlib.decompress(client_id)
    print(f"Decompressed length: {len(decompressed)} bytes")


# Test connection
async def test_connection():
    mqtt = InstagramMQTT(state)

    # Connect
    mqtt._client.connect("edge-mqtt.facebook.com", 443, keepalive=60)
    mqtt._client.loop_start()

    # Wait 10 seconds
    await asyncio.sleep(10)

    # Check if connected
    if mqtt._client.is_connected():
        print("Successfully connected!")
    else:
        print("Connection failed")

    mqtt._client.loop_stop()
```

---

This document provides all the essential code snippets you need to port the mautrix-instagram MQTT implementation. Start with the Thrift layer, then the MQTT client, then the connection logic.