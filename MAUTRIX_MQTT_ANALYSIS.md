# Mautrix-Instagram MQTT Implementation Analysis

**Source**: https://github.com/mautrix/instagram
**Analysis Date**: 2026-02-07
**Purpose**: Port working MQTT implementation to instagram-mcp project

---

## Executive Summary

The mautrix-instagram project contains a **production-ready, working Python implementation** of Instagram's MQTT realtime messaging protocol. It uses:
- **paho-mqtt** library as the base MQTT client
- **Custom MQTToT protocol** (MQTT-over-Thrift) for connection
- **Thrift Compact Protocol** for encoding/decoding payloads
- **asyncio** for async/await integration
- **zlib compression** for all payloads

This is the MOST RELIABLE reference for porting because it's actively maintained and used in production by the Matrix bridge community.

---

## Architecture Overview

### File Structure
```
mauigpapi/mqtt/
├── conn.py              # Main AndroidMQTT class (1014 lines)
├── otclient.py          # MQTToTClient - custom MQTT client (58 lines)
├── subscription.py      # Topic definitions and GraphQL subscriptions
├── events.py            # Event dataclasses (Connect, Disconnect, etc.)
├── thrift/
│   ├── autospec.py      # Automatic thrift_spec generation from attrs
│   ├── ig_objects.py    # RealtimeConfig, IncomingMessage structures
│   ├── type.py          # TType enum for Thrift types
│   ├── write.py         # ThriftWriter - Compact Protocol encoder (181 lines)
│   └── read.py          # ThriftReader - Compact Protocol decoder (71 lines)
```

---

## 1. MQTT Connection (MQTToT Protocol)

### Custom MQTT Client (`otclient.py`)

They extend `paho.mqtt.client.Client` and override `_send_connect()`:

```python
class MQTToTClient(paho.mqtt.client.Client):
    def _send_connect(self, keepalive):
        proto_ver = self._protocol
        protocol = b"MQTToT"  # NOT "MQTT" - this is the key!

        remaining_length = 2 + len(protocol) + 1 + 1 + 2 + len(self._client_id)

        # Username, password, clean session flags
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
        packet.extend(self._client_id)  # NO LENGTH PREFIX on client_id!

        self._keepalive = keepalive
        return self._packet_queue(command, packet, 0, 0)
```

**Key differences from standard MQTT:**
1. Protocol name is `"MQTToT"` instead of `"MQTT"`
2. Client ID is sent **without a length prefix** (raw bytes)
3. All other MQTT features removed (wills, etc.)

### Client Initialization (`conn.py` lines 109-157)

```python
def __init__(self, state: AndroidState, ...):
    self._client = MQTToTClient(
        client_id=self._form_client_id(),  # Thrift-encoded, zlib-compressed
        clean_session=True,
        protocol=pmc.MQTTv31,  # MQTT 3.1
        transport="tcp",
    )
    self._client.tls_set()  # Enable TLS
    self._client.connect_async("edge-mqtt.facebook.com", 443, keepalive=mqtt_keepalive)
```

**Connection details:**
- Host: `edge-mqtt.facebook.com`
- Port: `443` (TLS)
- Protocol: MQTT 3.1 (not 3.1.1 or 5.0)
- Keepalive: 60 seconds (configurable)

---

## 2. Client ID (CONNECT Payload)

### Thrift Structure (`ig_objects.py`)

The client_id is a **zlib-compressed Thrift-encoded** `RealtimeConfig` object:

```python
@autospec
@dataclass(kw_only=True)
class RealtimeConfig:
    client_identifier: str              # device.phone_id[:20]
    will_topic: str = None
    will_message: str = None
    client_info: RealtimeClientInfo     # Nested struct
    password: str                        # "authorization=Bearer ..."
    get_diffs_request: List[str] = None
    zero_rating_token_hash: str = None
    app_specific_info: Dict[str, str] = None  # Important metadata!
```

```python
@autospec
@dataclass(kw_only=True)
class RealtimeClientInfo:
    user_id: int = field(TType.I64)
    user_agent: str
    client_capabilities: int = field(TType.I64)        # 0b10110111
    endpoint_capabilities: int = field(TType.I64)      # 0
    publish_format: int = field(TType.I32)             # 1
    no_automatic_foreground: bool                       # True
    make_user_available_in_foreground: bool            # False
    device_id: str                                      # phone_id
    is_initially_foreground: bool                       # False
    network_type: int = field(TType.I32)               # 1
    network_subtype: int = field(TType.I32)            # -1
    client_mqtt_session_id: int = field(TType.I64)    # timestamp & 0xFFFFFFFF
    client_ip_address: str = None
    subscribe_topics: List[int] = field(TType.LIST, TType.I32)  # [88, 135, ...]
    client_type: str                                    # "cookie_auth"
    app_id: int = field(TType.I64)                     # 567067343352427
    device_secret: str                                  # ""
    client_stack: int = field(TType.BYTE)              # 3
    # ... more fields
```

### Building the Client ID (`conn.py` lines 186-237)

```python
def _form_client_id(self) -> bytes:
    subscribe_topics = [
        RealtimeTopic.PUBSUB,           # 88
        RealtimeTopic.SUB_IRIS_RESPONSE,  # 135
        RealtimeTopic.RS_REQ,           # 244
        RealtimeTopic.REALTIME_SUB,     # 149
        RealtimeTopic.REGION_HINT,      # 150
        RealtimeTopic.RS_RESP,          # 245
        RealtimeTopic.T_RTC_LOG,        # 274
        RealtimeTopic.SEND_MESSAGE_RESPONSE,  # 133
        RealtimeTopic.MESSAGE_SYNC,     # 146  <- THE IMPORTANT ONE
        RealtimeTopic.LIGHTSPEED_RESPONSE,  # 179
        RealtimeTopic.UNKNOWN_PP,       # 34
    ]
    subscribe_topic_ids = [int(topic.encoded) for topic in subscribe_topics]

    password = f"authorization={self.state.session.authorization}"

    cfg = RealtimeConfig(
        client_identifier=self.state.device.phone_id[:20],
        client_info=RealtimeClientInfo(
            user_id=int(self.state.user_id),
            user_agent=self.state.user_agent,
            client_capabilities=0b10110111,
            endpoint_capabilities=0,
            publish_format=1,
            no_automatic_foreground=True,
            make_user_available_in_foreground=False,
            device_id=self.state.device.phone_id,
            is_initially_foreground=False,
            network_type=1,
            network_subtype=-1,
            client_mqtt_session_id=int(time.time() * 1000) & 0xFFFFFFFF,
            subscribe_topics=subscribe_topic_ids,
            client_type="cookie_auth",
            app_id=567067343352427,
            device_secret="",
            client_stack=3,
        ),
        password=password,
        app_specific_info={
            "capabilities": self.state.application.CAPABILITIES,
            "app_version": self.state.application.APP_VERSION,
            "everclear_subscriptions": json.dumps(everclear_subscriptions),
            "User-Agent": self.state.user_agent,
            "Accept-Language": self.state.device.language.replace("_", "-"),
            "platform": "android",
            "ig_mqtt_route": "django",
            "pubsub_msg_type_blacklist": "direct, typing_type",
            "auth_cache_enabled": "1",
        },
    )

    # Encode to Thrift, then compress with zlib level 9
    return zlib.compress(cfg.to_thrift(), level=9)
```

**Critical fields:**
- `subscribe_topics`: Must include `146` for `/ig_message_sync`
- `password`: Instagram Bearer token from session
- `app_specific_info`: Contains critical metadata like app version, capabilities
- `client_mqtt_session_id`: Timestamp-based session ID

---

## 3. Thrift Compact Protocol Implementation

### ThriftWriter (`thrift/write.py`)

Complete implementation of Thrift Compact Protocol encoding:

```python
class ThriftWriter(io.BytesIO):
    prev_field_id: int
    stack: list[int]

    @staticmethod
    def _to_zigzag(val: int, bits: int) -> int:
        """Zigzag encoding for signed integers"""
        return (val << 1) ^ (val >> (bits - 1))

    def _write_varint(self, val: int) -> None:
        """Variable-length integer encoding"""
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

    def write_field_begin(self, field_id: int, ttype: TType) -> None:
        """Write field header with delta encoding"""
        ttype_val = ttype.value
        delta = field_id - self.prev_field_id
        if 0 < delta < 16:
            # Small delta: pack into single byte
            self._write_byte((delta << 4) | ttype_val)
        else:
            # Large delta: type byte + zigzag field ID
            self._write_byte(ttype_val)
            self._write_word(field_id)
        self.prev_field_id = field_id

    def write_struct(self, obj: Any) -> None:
        """Write a struct using thrift_spec metadata"""
        for field_id in iter(obj.thrift_spec):
            field_type, field_name, inner_type = obj.thrift_spec[field_id]

            val = getattr(obj, field_name, None)
            if val is None:
                continue  # Skip None fields

            # Write based on type
            if field_type == TType.BOOL:
                self.write_field_begin(field_id, TType.TRUE if val else TType.FALSE)
            elif field_type in (TType.BYTE, TType.I16, TType.I32, TType.I64, TType.BINARY):
                self.write_val(field_id, field_type, val)
            elif field_type in (TType.LIST, TType.SET):
                self.write_list(field_id, inner_type, val)
            elif field_type == TType.MAP:
                (key_type, _), (value_type, _) = inner_type
                self.write_map(field_id, key_type, value_type, val)
            elif field_type == TType.STRUCT:
                self.write_struct_begin(field_id)
                self.write_struct(val)

        self.write_stop()  # End of struct marker
```

**Key features:**
- Delta encoding for field IDs (saves space)
- Zigzag encoding for signed integers
- Variable-length integers (varint)
- Boolean values encoded as field type (TType.TRUE/FALSE)
- Automatic struct traversal using `thrift_spec`

### ThriftReader (`thrift/read.py`)

Decoding for incoming messages:

```python
class ThriftReader(io.BytesIO):
    @staticmethod
    def _from_zigzag(val: int) -> int:
        return (val >> 1) ^ -(val & 1)

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
        """Read field header and return type"""
        byte = self._read_byte()
        if byte == 0:
            return TType.STOP  # End of struct

        delta = (byte & 0xF0) >> 4
        if delta == 0:
            # Large delta: read field ID
            self.prev_field_id = self._from_zigzag(self.read_varint())
        else:
            # Small delta: increment
            self.prev_field_id += delta

        return TType(byte & 0x0F)
```

---

## 4. Topic Handling

### Topic Encoding (`subscription.py` lines 330-376)

Topics are mapped to numeric IDs:

```python
_topic_map: dict[str, str] = {
    "/pp": "34",
    "/ig_sub_iris": "134",
    "/ig_sub_iris_response": "135",
    "/ig_message_sync": "146",        # THE MAIN ONE FOR DMs
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

class RealtimeTopic(Enum):
    MESSAGE_SYNC = "/ig_message_sync"  # Topic 146
    # ... others

    @property
    def encoded(self) -> str:
        return _topic_map[self.value]

    @staticmethod
    def decode(val: str) -> RealtimeTopic:
        return RealtimeTopic(_reverse_topic_map[val])
```

### Message Handler (`conn.py` lines 493-531)

```python
def _on_message_handler(self, client: MQTToTClient, _: Any, message: pmc.MQTTMessage) -> None:
    try:
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
        # ... other topics
    except Exception:
        self.log.exception("Error in incoming MQTT message handler")
```

**Critical**: ALL payloads are zlib-compressed!

---

## 5. Message Sync Payload Parsing (`/ig_message_sync`)

### Format (`conn.py` lines 391-408)

```python
def _on_message_sync(self, payload: bytes) -> None:
    # Payload is JSON after decompression
    parsed = json.loads(payload.decode("utf-8"))

    # parsed is a list of IrisPayload objects
    for sync_item in parsed:
        parsed_item = IrisPayload.deserialize(sync_item)

        # Update sequence ID
        if self._iris_seq_id < parsed_item.seq_id:
            self._iris_seq_id = parsed_item.seq_id
            self._iris_snapshot_at_ms = int(time.time() * 1000)
            background_task.create(
                self._dispatch(NewSequenceID(self._iris_seq_id, self._iris_snapshot_at_ms))
            )

        # Process each data item in the payload
        for part in parsed_item.data:
            self._on_messager_sync_item(part, parsed_item)
```

### IrisPayload Structure (from `types/mqtt.py`)

```python
@dataclass(kw_only=True)
class IrisPayload(SerializableAttrs):
    data: List[IrisPayloadData]
    message_type: int
    seq_id: int                    # IMPORTANT: Sequence ID for resumption
    event: str = "patch"
    mutation_token: Optional[str] = None
    realtime: Optional[bool] = None
    sampled: Optional[bool] = None

@dataclass(kw_only=True)
class IrisPayloadData(SerializableAttrs):
    op: Operation               # ADD, REPLACE, REMOVE
    path: str                   # e.g., "/direct_v2/threads/340282366841710300949128194755493044420/items/28446322987695433556816519053594624"
    value: str = "{}"           # JSON-encoded data
```

### Path Parsing (`conn.py` lines 309-337)

```python
def _parse_direct_thread_path(self, path: str) -> dict:
    # Path format: /direct_v2/threads/{thread_id}/[subpath]
    blank, direct_v2, threads, thread_id, *rest = path.split("/")

    additional = {"thread_id": thread_id}

    if rest:
        subitem_key = rest[0]
        if subitem_key == "items":
            additional["item_id"] = rest[1]
            if len(rest) > 4 and rest[2] == "reactions":
                # Reaction path
                additional["reaction_type"] = ReactionType(rest[3])
                additional["reaction_user_id"] = int(rest[4])
        elif subitem_key == "participants" and len(rest) > 2 and rest[2] == "has_seen":
            additional["has_seen"] = int(rest[1])
        elif subitem_key == "activity_indicator_id":
            additional["activity_indicator_id"] = rest[1]

    return additional
```

**Path examples:**
- New message: `/direct_v2/threads/{thread_id}/items/{item_id}`
- Reaction: `/direct_v2/threads/{thread_id}/items/{item_id}/reactions/{type}/{user_id}`
- Read receipt: `/direct_v2/threads/{thread_id}/participants/{user_id}/has_seen`
- Thread update: `/direct_v2/inbox/threads/{thread_id}`

---

## 6. Iris Subscribe (Resume from Sequence ID)

After connecting, must subscribe to the message sync stream:

```python
async def iris_subscribe(self, seq_id: int, snapshot_at_ms: int) -> None:
    resp = await self.request(
        RealtimeTopic.SUB_IRIS,           # Topic 134
        RealtimeTopic.SUB_IRIS_RESPONSE,  # Response on topic 135
        {
            "seq_id": seq_id,
            "snapshot_at_ms": snapshot_at_ms,
            "snapshot_app_version": self.state.application.APP_VERSION,
            "timezone_offset": int(self.state.device.timezone_offset),
            "subscription_type": "message",
        },
        timeout=20,
    )

    resp_dict = json.loads(resp.payload.decode("utf-8"))
    if resp_dict["error_type"] and resp_dict["error_message"]:
        raise IrisSubscribeError(resp_dict["error_type"], resp_dict["error_message"])

    # Server may return a newer seq_id
    latest_seq_id = resp_dict.get("latest_seq_id")
    if latest_seq_id > self._iris_seq_id:
        self._iris_seq_id = latest_seq_id
```

**Sequence ID management:**
- Start with `seq_id=0` for first connection
- Save `seq_id` after each message
- On reconnect, send saved `seq_id` to resume
- Server returns missed messages if any

---

## 7. Publishing Messages

### Format (`conn.py` lines 684-698)

```python
def publish(self, topic: RealtimeTopic, payload: str | bytes | dict) -> asyncio.Future:
    if isinstance(payload, dict):
        payload = json.dumps(payload)
    if isinstance(payload, str):
        payload = payload.encode("utf-8")

    # ALL outgoing payloads are zlib-compressed!
    payload = zlib.compress(payload, level=9)

    # Publish with QoS 1 (at least once delivery)
    info = self._client.publish(topic.encoded, payload, qos=1)

    # Create future for PUBACK
    fut = self._loop.create_future()
    self._publish_waiters[info.mid] = fut
    return fut
```

### Sending Text Messages (`conn.py` lines 946-978)

```python
def send_text(
    self,
    thread_id: str,
    text: str = "",
    urls: list[str] | None = None,
    shh_mode: bool = False,
    client_context: str | None = None,
    replied_to_item_id: str | None = None,
    replied_to_client_context: str | None = None,
    mentioned_user_ids: list[int] | None = None,
) -> Awaitable[CommandResponse]:
    args = {"text": text}
    item_type = ThreadItemType.TEXT

    if urls is not None:
        args = {
            "link_text": text,
            "link_urls": json.dumps(urls or []),
        }
        item_type = ThreadItemType.LINK

    if mentioned_user_ids:
        args["mentioned_user_ids"] = json.dumps([str(x) for x in mentioned_user_ids])
        args["sampled"] = True

    return self.send_item(
        thread_id,
        **args,
        shh_mode=shh_mode,
        item_type=item_type,
        client_context=client_context,
        replied_to_item_id=replied_to_item_id,
        replied_to_client_context=replied_to_client_context,
    )
```

Publishes to topic 132 (`/ig_send_message`), expects response on topic 133 (`/ig_send_message_response`).

---

## 8. Asyncio Integration

### Socket Handling (`conn.py` lines 241-251)

They integrate paho-mqtt with asyncio using event loop callbacks:

```python
def _on_socket_open(self, client: MQTToTClient, _: Any, sock: socket) -> None:
    self._loop.add_reader(sock, client.loop_read)

def _on_socket_close(self, client: MQTToTClient, _: Any, sock: socket) -> None:
    self._loop.remove_reader(sock)

def _on_socket_register_write(self, client: MQTToTClient, _: Any, sock: socket) -> None:
    self._loop.add_writer(sock, client.loop_write)

def _on_socket_unregister_write(self, client: MQTToTClient, _: Any, sock: socket) -> None:
    self._loop.remove_writer(sock)
```

### Main Listen Loop (`conn.py` lines 577-655)

```python
async def listen(
    self,
    graphql_subs: set[str] | None = None,
    skywalker_subs: set[str] | None = None,
    seq_id: int = None,
    snapshot_at_ms: int = None,
    retry_limit: int = 10,
) -> None:
    self._graphql_subs = graphql_subs or set()
    self._skywalker_subs = skywalker_subs or set()
    self._iris_seq_id = seq_id
    self._iris_snapshot_at_ms = snapshot_at_ms

    async def connect_and_watch():
        await self._reconnect()

        while True:
            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                self.disconnect()
                return

            rc = self._client.loop_misc()  # Process MQTT events

            # Check for disconnection
            if self._client._state == pmc.mqtt_cs_disconnecting:
                return

            # Handle errors
            if rc != pmc.MQTT_ERR_SUCCESS:
                if rc == pmc.MQTT_ERR_CONN_LOST:
                    raise MQTTNotConnected("MQTT_ERR_CONN_LOST")
                # ... handle other errors

    # Retry with exponential backoff
    await proxy_with_retry(
        "mqtt.listen",
        lambda: connect_and_watch(),
        logger=self.log,
        max_retries=retry_limit,
        retryable_exceptions=(MQTTNotConnected, MQTTReconnectionError),
        max_wait_seconds=5,
        multiply_wait_seconds=1,
        reset_after_seconds=3600,
    )
```

---

## 9. Dependencies

From `requirements.txt`:

```
paho-mqtt>=1.5,<2        # MQTT client library
attrs>=20.1              # Dataclass decorators
yarl>=1,<2               # URL parsing
aiohttp>=3,<4            # (for HTTP API, not MQTT)
```

**Key dependencies for porting:**
1. **paho-mqtt**: Standard Python MQTT library
2. **attrs**: For dataclass definitions with metadata
3. Standard library: `zlib`, `json`, `asyncio`, `struct`, `io`

---

## 10. Critical Implementation Details

### 1. Compression
- **ALL** MQTT payloads (incoming and outgoing) are zlib-compressed
- Use `zlib.compress(data, level=9)` for outgoing
- Use `zlib.decompress(data)` for incoming

### 2. Protocol Name
- Must use `"MQTToT"` not `"MQTT"` in CONNECT packet
- Client ID has NO length prefix (unlike standard MQTT)

### 3. Client ID Construction
```
1. Create RealtimeConfig object with all metadata
2. Encode to Thrift Compact Protocol (cfg.to_thrift())
3. Compress with zlib level 9
4. Use as client_id in CONNECT
```

### 4. Topics
- Subscribe to topic `146` (`/ig_message_sync`) in client_id
- All topics are numeric strings ("146", "88", etc.)
- Must decode topic from numeric to name for routing

### 5. Sequence IDs
- Extract `seq_id` from every message
- Save to persistent storage
- Send in iris_subscribe on reconnect
- Enables message recovery after disconnect

### 6. Message Format
```
Incoming: zlib → JSON → IrisPayload (list) → IrisPayloadData items
Outgoing: dict → JSON → zlib → MQTT publish
```

### 7. Authentication
- Pass Bearer token in `password` field of RealtimeConfig
- Format: `"authorization=Bearer <token>"`

### 8. Asyncio Integration
- Use `loop.add_reader/add_writer` for socket
- Call `client.loop_read/loop_write` on socket events
- Call `client.loop_misc()` periodically (every 1 sec)

---

## 11. Porting Checklist

### Phase 1: Thrift Encoding
- [ ] Port ThriftWriter class (write.py)
- [ ] Port ThriftReader class (read.py)
- [ ] Port TType enum (type.py)
- [ ] Port autospec decorator (autospec.py)
- [ ] Port RealtimeConfig and RealtimeClientInfo (ig_objects.py)
- [ ] Test Thrift encoding/decoding with known payloads

### Phase 2: MQTT Client
- [ ] Port MQTToTClient (otclient.py)
- [ ] Test CONNECT packet format
- [ ] Verify "MQTToT" protocol name
- [ ] Verify client_id has no length prefix

### Phase 3: Connection Logic
- [ ] Port topic definitions (subscription.py)
- [ ] Port _form_client_id() function
- [ ] Implement TLS connection to edge-mqtt.facebook.com:443
- [ ] Test successful CONNACK

### Phase 4: Message Handling
- [ ] Implement zlib decompression on all incoming
- [ ] Parse MESSAGE_SYNC (topic 146) payloads
- [ ] Parse IrisPayload JSON structure
- [ ] Implement path parsing for thread/item IDs
- [ ] Extract message content from "value" field

### Phase 5: Sending Messages
- [ ] Implement publish() with zlib compression
- [ ] Implement send_text() function
- [ ] Implement send_command() base function
- [ ] Handle SEND_MESSAGE_RESPONSE (topic 133)

### Phase 6: State Management
- [ ] Save/load sequence ID
- [ ] Implement iris_subscribe()
- [ ] Handle reconnection with seq_id
- [ ] Implement sequence ID updates

### Phase 7: Asyncio Integration
- [ ] Implement socket callbacks (add_reader/writer)
- [ ] Implement listen() loop
- [ ] Add retry logic with exponential backoff
- [ ] Handle disconnection/reconnection

### Phase 8: Testing
- [ ] Test CONNECT with real Instagram account
- [ ] Test receiving messages
- [ ] Test sending messages
- [ ] Test reconnection after disconnect
- [ ] Test seq_id persistence and recovery

---

## 12. Key Files to Port (Priority Order)

1. **thrift/type.py** (37 lines) - Enum definitions
2. **thrift/write.py** (181 lines) - Thrift encoding
3. **thrift/read.py** (71 lines) - Thrift decoding
4. **thrift/autospec.py** (95 lines) - Metadata decorator
5. **thrift/ig_objects.py** (113 lines) - Data structures
6. **subscription.py** (377 lines) - Topic definitions
7. **otclient.py** (58 lines) - Custom MQTT client
8. **conn.py** (1014 lines) - Main logic (port incrementally)

**Total lines to port**: ~2,000 lines of Python

---

## 13. Testing Strategy

### Unit Tests
1. Test Thrift encoding/decoding with known payloads
2. Test zigzag encoding/decoding
3. Test varint encoding/decoding
4. Test topic encoding/decoding
5. Test path parsing

### Integration Tests
1. Test CONNECT packet construction
2. Test client_id formation
3. Test zlib compression/decompression
4. Test JSON parsing

### Live Tests
1. Connect to Instagram MQTT
2. Subscribe to topics
3. Receive a message
4. Send a message
5. Disconnect and reconnect with seq_id

---

## 14. Common Pitfalls to Avoid

1. **Forgetting zlib compression**: ALL payloads are compressed
2. **Wrong protocol name**: Must be "MQTToT" not "MQTT"
3. **Client ID length**: No length prefix (unlike standard MQTT)
4. **Topic encoding**: Topics are numeric strings, not paths
5. **Sequence ID**: Must save and restore, or lose messages
6. **Delta encoding**: Field IDs use delta encoding in Thrift
7. **Zigzag encoding**: Signed integers use zigzag, not two's complement
8. **Boolean encoding**: Booleans are encoded as field types, not values

---

## 15. Reference Constants

### App ID
```python
app_id = 567067343352427
```

### Client Capabilities
```python
client_capabilities = 0b10110111  # 183 in decimal
```

### Subscribe Topics (all required)
```python
[88, 135, 244, 149, 150, 245, 274, 133, 146, 179, 34]
```

### Everclear Subscriptions
```python
{
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

## Conclusion

This is a **complete, working implementation** that has been battle-tested in production. The code is clean, well-structured, and thoroughly documented.

**Key advantages:**
1. Pure Python (no compiled dependencies except standard libs)
2. Uses standard paho-mqtt library
3. Asyncio-native
4. Complete Thrift implementation
5. Handles all edge cases (reconnection, seq_id, compression, etc.)

**Recommended approach:**
1. Port Thrift layer first (it's self-contained)
2. Port MQTToT client second
3. Port connection logic third
4. Test incrementally with real Instagram connection
5. Add message handling last

The total effort is approximately 2,000 lines of carefully-ported Python code, but the reference implementation is so complete that it should be straightforward.