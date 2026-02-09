# Instagram MQTT Data Flow Examples

**Purpose**: Detailed examples of actual data flowing through the Instagram MQTT protocol

---

## 1. Connection Flow

### Step 1: Build Client ID

**Input (Python objects):**
```python
RealtimeConfig(
    client_identifier="12345678901234567890",  # phone_id[:20]
    client_info=RealtimeClientInfo(
        user_id=123456789,
        user_agent="Instagram 275.0.0.27.98 Android (...)",
        client_capabilities=183,  # 0b10110111
        endpoint_capabilities=0,
        publish_format=1,
        no_automatic_foreground=True,
        make_user_available_in_foreground=False,
        device_id="android-1234567890abcdef",
        is_initially_foreground=False,
        network_type=1,
        network_subtype=-1,
        client_mqtt_session_id=1738900000000,  # timestamp in ms & 0xFFFFFFFF
        subscribe_topics=[88, 135, 244, 149, 150, 245, 274, 133, 146, 179, 34],
        client_type="cookie_auth",
        app_id=567067343352427,
        device_secret="",
        client_stack=3,
    ),
    password="authorization=Bearer IGT:2:eyJ...",
    app_specific_info={
        "capabilities": "3brTv10=",
        "app_version": "275.0.0.27.98",
        "everclear_subscriptions": '{"inapp_notification_subscribe_comment": "17899377895239777", ...}',
        "User-Agent": "Instagram 275.0.0.27.98 Android (...)",
        "Accept-Language": "en-US",
        "platform": "android",
        "ig_mqtt_route": "django",
        "pubsub_msg_type_blacklist": "direct, typing_type",
        "auth_cache_enabled": "1",
    }
)
```

**After Thrift Compact Protocol encoding:**
```
Raw bytes (hex, first 100 bytes):
18 31 32 33 34 35 36 37 38 39 30 31 32 33 34 35 36 37 38 39 30
4c 18 f5 d3 c4 dd 07 18 49 6e 73 74 61 67 72 61 6d 20 32 37 ...
(~500-800 bytes depending on fields)
```

**After zlib compression (level 9):**
```
Compressed bytes (hex, first 100 bytes):
78 da ed 5a 6d 6f 1b 37 10 fe 2b 96 e3 49 52 52 10 14 45 d1 ...
(~300-500 bytes after compression)
```

**This becomes the MQTT client_id field**

### Step 2: Build CONNECT Packet

**MQTT CONNECT packet structure:**
```
Fixed Header:
  0x10                    # CONNECT packet type

Remaining Length:
  <varint>                # Variable length encoding

Variable Header:
  0x00 0x06               # Protocol name length (6)
  0x4d 0x51 0x54 0x54     # "MQTToT" (NOT "MQTT"!)
  0x6f 0x54
  0x03                    # Protocol version (MQTT 3.1)
  0xc2                    # Connect flags (0x80 + 0x40 + 0x02)
                          #   - Username flag
                          #   - Password flag
                          #   - Clean session
  0x00 0x3c               # Keepalive (60 seconds)

Payload:
  <client_id>             # Raw bytes (NO length prefix!)
                          # This is the zlib-compressed Thrift data
```

**Example hex dump of full CONNECT packet:**
```
10                        # CONNECT
82 04                     # Remaining length (514 bytes)
00 06 4d 51 54 54 6f 54  # Protocol name length + "MQTToT"
03                        # Protocol version
c2                        # Connect flags
00 3c                     # Keepalive
78 da ed 5a 6d 6f 1b 37  # Client ID (zlib-compressed Thrift)
10 fe 2b 96 e3 49 52 52  # ... (continues for ~500 bytes)
...
```

### Step 3: Server Response (CONNACK)

**On success:**
```
20 02 00 00               # CONNACK, Session Present=0, Return Code=0 (success)
```

**On auth failure:**
```
20 02 00 05               # CONNACK, Return Code=5 (not authorized)
```

---

## 2. Iris Subscribe Flow

### Request to Topic 134 (/ig_sub_iris)

**Payload (before compression):**
```json
{
    "seq_id": 0,
    "snapshot_at_ms": 1738900000000,
    "snapshot_app_version": "275.0.0.27.98",
    "timezone_offset": -28800,
    "subscription_type": "message"
}
```

**After JSON encoding and zlib compression:**
```
PUBLISH packet to topic "134":
  Topic: "134" (3 bytes: "1", "3", "4")
  QoS: 1 (at least once)
  Payload: <zlib compressed JSON>
```

### Response from Topic 135 (/ig_sub_iris_response)

**Decompressed payload:**
```json
{
    "error_type": null,
    "error_message": null,
    "latest_seq_id": 123456789,
    "subscribed_at_ms": 1738900005000
}
```

**If error:**
```json
{
    "error_type": "invalid_seq_id",
    "error_message": "Sequence ID too old"
}
```

---

## 3. Receiving Messages (Topic 146 - /ig_message_sync)

### Example 1: New Text Message

**Compressed payload received on topic 146:**
```
78 9c ab 56 4a 49 2c 49 54 b2 52 8a 56 ca 2f 50 b2 52 4a 4c 49 51 d2 51 28 00 ...
(zlib-compressed JSON)
```

**After zlib decompression:**
```json
[
    {
        "data": [
            {
                "op": "add",
                "path": "/direct_v2/threads/340282366841710300949128194755493044420/items/28446322987695433556816519053594624",
                "value": "{\"item_id\":\"28446322987695433556816519053594624\",\"user_id\":987654321,\"timestamp\":\"1738900010000000\",\"item_type\":\"text\",\"text\":\"Hello world!\"}"
            }
        ],
        "message_type": 1,
        "seq_id": 123456790,
        "event": "patch"
    }
]
```

**Parsed structure:**
- `seq_id`: 123456790 (increment from previous)
- `path`: Thread 340282366841710300949128194755493044420, Item 28446322987695433556816519053594624
- `value`: JSON-encoded message data
  - `item_id`: Unique message ID
  - `user_id`: Sender ID
  - `timestamp`: Microseconds since epoch
  - `item_type`: "text"
  - `text`: Message content

### Example 2: Message Reaction

**Decompressed payload:**
```json
[
    {
        "data": [
            {
                "op": "add",
                "path": "/direct_v2/threads/340282366841710300949128194755493044420/items/28446322987695433556816519053594624/reactions/like/987654321",
                "value": "{\"sender_id\":987654321,\"timestamp\":\"1738900015000000\",\"emoji\":\"❤️\"}"
            }
        ],
        "message_type": 1,
        "seq_id": 123456791,
        "event": "patch"
    }
]
```

**Path breakdown:**
```
/direct_v2/threads/{thread_id}/items/{item_id}/reactions/{type}/{user_id}
                    ^^^^^^^^^^^^^^^^      ^^^^^^^^^^^^^^^^^       ^^^^ ^^^^^^^^^
                    Thread ID             Message ID          Type  User who reacted
```

### Example 3: Read Receipt

**Decompressed payload:**
```json
[
    {
        "data": [
            {
                "op": "replace",
                "path": "/direct_v2/threads/340282366841710300949128194755493044420/participants/987654321/has_seen",
                "value": "{\"item_id\":\"28446322987695433556816519053594624\",\"timestamp\":\"1738900020000000\"}"
            }
        ],
        "message_type": 1,
        "seq_id": 123456792,
        "event": "patch"
    }
]
```

**Meaning:** User 987654321 has read up to item 28446322987695433556816519053594624

### Example 4: Typing Indicator

**Decompressed payload:**
```json
[
    {
        "data": [
            {
                "op": "replace",
                "path": "/direct_v2/threads/340282366841710300949128194755493044420/activity_indicator_id/1738900025000",
                "value": "{\"timestamp\":\"1738900025000000\",\"sender_id\":\"987654321\",\"ttl\":10000,\"activity_status\":1}"
            }
        ],
        "message_type": 1,
        "seq_id": 123456793,
        "event": "patch"
    }
]
```

**Activity status values:**
- `0`: OFF (stopped typing)
- `1`: TEXT (typing text)
- `2`: VISUAL (recording voice/video)

### Example 5: Thread Update

**Decompressed payload:**
```json
[
    {
        "data": [
            {
                "op": "replace",
                "path": "/direct_v2/inbox/threads/340282366841710300949128194755493044420",
                "value": "{\"thread_id\":\"340282366841710300949128194755493044420\",\"thread_title\":\"New Group Name\",\"last_activity_at\":\"1738900030000000\"}"
            }
        ],
        "message_type": 1,
        "seq_id": 123456794,
        "event": "patch"
    }
]
```

### Example 6: Message Delete

**Decompressed payload:**
```json
[
    {
        "data": [
            {
                "op": "remove",
                "path": "/direct_v2/threads/340282366841710300949128194755493044420/items/28446322987695433556816519053594624",
                "value": "{}"
            }
        ],
        "message_type": 1,
        "seq_id": 123456795,
        "event": "patch"
    }
]
```

**Operations:**
- `add`: New item created
- `replace`: Existing item updated
- `remove`: Item deleted

---

## 4. Sending Messages (Topic 132 - /ig_send_message)

### Sending Text Message

**Payload to send (before compression):**
```json
{
    "thread_id": "340282366841710300949128194755493044420",
    "client_context": "7046234567890123456",
    "offline_threading_id": "7046234567890123456",
    "action": "send_item",
    "item_type": "text",
    "text": "Hello from MQTT!",
    "is_shh_mode": "0"
}
```

**After zlib compression:**
```
PUBLISH to topic "132" (QoS 1):
  78 9c ab 56 2a c9 28 4a 4d 4c 89 cf cc 51 b2 52 32 36 30 32 38 ...
```

### Response on Topic 133 (/ig_send_message_response)

**Success response (decompressed):**
```json
{
    "action": "send_item",
    "status": "ok",
    "payload": {
        "client_context": "7046234567890123456",
        "item_id": "28446322987695433556816519053594625",
        "timestamp": "1738900035000000",
        "thread_id": "340282366841710300949128194755493044420"
    }
}
```

**Error response:**
```json
{
    "action": "send_item",
    "status": "error",
    "status_code": "400",
    "message": "Invalid thread_id",
    "exception": "ClientError"
}
```

---

## 5. Topic 88 (/pubsub) - Activity Indicators

### Typing Event

**Received on topic 88 (decompressed):**
```
Thrift-encoded message (after decompression):
  Topic: "/direct_v2/threads/340282366841710300949128194755493044420/activity_indicator_id/1738900040000"
  Payload: JSON string
```

**Thrift structure:**
```python
IncomingMessage(
    topic="/direct_v2/threads/.../activity_indicator_id/...",
    payload='{"data":[{"path":"...","value":"...","op":"replace"}]}'
)
```

**Payload JSON (after Thrift decode):**
```json
{
    "data": [
        {
            "path": "/direct_v2/threads/340282366841710300949128194755493044420/activity_indicator_id/1738900040000",
            "value": "{\"timestamp\":\"1738900040000000\",\"sender_id\":\"987654321\",\"ttl\":10000,\"activity_status\":1}",
            "op": "replace",
            "doublePublish": false
        }
    ],
    "event": "patch"
}
```

---

## 6. Sequence ID Management

### Initial Connection (seq_id = 0)

**Iris subscribe request:**
```json
{
    "seq_id": 0,
    "snapshot_at_ms": 1738900000000,
    "snapshot_app_version": "275.0.0.27.98",
    "timezone_offset": -28800,
    "subscription_type": "message"
}
```

**Server response:**
```json
{
    "latest_seq_id": 123456789
}
```

**Meaning:** You were at 0, server is at 123456789. You'll get all messages from your inbox.

### During Session

**Each message includes seq_id:**
```json
{
    "data": [...],
    "seq_id": 123456790  // Increment by 1
}
```

**Save this seq_id after processing!**

### Reconnection with Saved seq_id

**Iris subscribe request:**
```json
{
    "seq_id": 123456790,
    "snapshot_at_ms": 1738900050000,
    "snapshot_app_version": "275.0.0.27.98",
    "timezone_offset": -28800,
    "subscription_type": "message"
}
```

**Server response (if messages were missed):**
```json
{
    "latest_seq_id": 123456810
}
```

**Then you receive catchup messages:**
```json
[
    {"data": [...], "seq_id": 123456791},
    {"data": [...], "seq_id": 123456792},
    ...
    {"data": [...], "seq_id": 123456810}
]
```

**If seq_id is too old:**
```json
{
    "error_type": "invalid_seq_id",
    "error_message": "Sequence ID expired, use 0 for full sync"
}
```

---

## 7. Complete Message Example with All Layers

### Raw MQTT PUBLISH packet (received)

```
Hex dump:
30 82 01 a3               # PUBLISH, DUP=0, QoS=0, RETAIN=0
00 03 31 34 36            # Topic: "146" (3 bytes)
78 9c ab 56 4a 49 2c 49  # Payload starts (zlib compressed)
54 b2 52 8a 56 ca 2f 50
... (continues for 400+ bytes)
```

### Layer 1: MQTT Decode

```
Topic: "146" (MESSAGE_SYNC)
Payload: <419 bytes of zlib-compressed data>
```

### Layer 2: Zlib Decompress

```json
[
    {
        "data": [
            {
                "op": "add",
                "path": "/direct_v2/threads/340282366841710300949128194755493044420/items/28446322987695433556816519053594624",
                "value": "{\"item_id\":\"28446322987695433556816519053594624\",\"user_id\":987654321,\"timestamp\":\"1738900060000000\",\"item_type\":\"text\",\"text\":\"Test message\",\"client_context\":\"7046234567890123457\"}"
            }
        ],
        "message_type": 1,
        "seq_id": 123456791,
        "event": "patch",
        "mutation_token": null,
        "realtime": true
    }
]
```

### Layer 3: Parse JSON

```python
parsed = json.loads(payload)
# parsed is a list

for sync_item in parsed:
    seq_id = sync_item["seq_id"]  # 123456791

    for data_item in sync_item["data"]:
        op = data_item["op"]  # "add"
        path = data_item["path"]
        value = data_item["value"]  # JSON string!

        # Parse value
        message_data = json.loads(value)
```

### Layer 4: Extract Message

```python
{
    "item_id": "28446322987695433556816519053594624",
    "user_id": 987654321,
    "timestamp": "1738900060000000",
    "item_type": "text",
    "text": "Test message",
    "client_context": "7046234567890123457"
}
```

### Layer 5: Parse Path

```python
path_parts = path.split("/")
# ['', 'direct_v2', 'threads', '340282366841710300949128194755493044420', 'items', '28446322987695433556816519053594624']

thread_id = path_parts[3]  # '340282366841710300949128194755493044420'
item_id = path_parts[5]    # '28446322987695433556816519053594624'
```

### Final Parsed Message

```python
{
    "thread_id": "340282366841710300949128194755493044420",
    "item_id": "28446322987695433556816519053594624",
    "user_id": 987654321,
    "timestamp": 1738900060000000,  # microseconds
    "item_type": "text",
    "text": "Test message",
    "seq_id": 123456791
}
```

---

## 8. Error Scenarios

### Connection Refused (Wrong Auth Token)

```
CONNACK:
  20 02 00 05  # Return code 5 (Not authorized)
```

**Action:** Refresh Instagram session token

### Invalid Sequence ID

**Iris subscribe response:**
```json
{
    "error_type": "invalid_seq_id",
    "error_message": "Sequence ID is too old"
}
```

**Action:** Reset to seq_id=0, do full sync

### Message Send Failure

**Response on topic 133:**
```json
{
    "action": "send_item",
    "status": "error",
    "status_code": "404",
    "message": "Thread not found",
    "exception": "IGNotFoundException"
}
```

### Reconnection Signal (Topic 34 - /pp)

**Received on topic 34:**
```
(Empty or minimal payload)
```

**Meaning:** Server wants you to reconnect. Disconnect and reconnect immediately.

---

## 9. Compression Examples

### Before Compression

```json
{"thread_id":"340282366841710300949128194755493044420","client_context":"7046234567890123456","action":"send_item","item_type":"text","text":"Hello"}
```

**Size: 151 bytes**

### After zlib.compress(data, level=9)

```
78 9c ab 56 2a c9 28 4a 4d 4c 89 cf cc 51 b2 52 32 36 30 32 38
32 36 36 38 34 31 37 31 30 33 30 30 39 34 39 31 32 38 31 39 34
37 35 35 34 39 33 30 34 34 34 32 30 d2 51 4a ce c9 4c cd 2b 89
...
```

**Size: 127 bytes (16% reduction)**

For larger messages with repeated fields, compression ratio improves significantly (30-50% reduction).

---

## 10. Byte-Level Thrift Example

### Simple Struct

```python
# Python object
RealtimeClientInfo(
    user_id=123,
    user_agent="Test"
)
```

### Thrift Compact Protocol Encoding

```
Field 1 (user_id, I64):
  16           # Field header: delta=1, type=I64(6)
  f6 01        # Zigzag varint: (123 << 1) = 246 = 0xf6

Field 2 (user_agent, BINARY):
  28           # Field header: delta=2, type=BINARY(8)
  04           # String length: 4
  54 65 73 74  # "Test"

End marker:
  00           # STOP
```

**Total: 10 bytes**

### Decoding

```python
reader = ThriftReader(b'\x16\xf6\x01\x28\x04Test\x00')

field_type = reader.read_field()
# field_type = TType.I64, field_id = 1
user_id = reader.read_small_int()  # 123

field_type = reader.read_field()
# field_type = TType.BINARY, field_id = 2
length = reader.read_varint()  # 4
user_agent = reader.read(length)  # b'Test'

field_type = reader.read_field()
# field_type = TType.STOP
```

---

## Summary: Data Flow Overview

```
┌─────────────────────┐
│  Python Objects     │  RealtimeConfig(...)
└──────────┬──────────┘
           │
           v
┌─────────────────────┐
│  Thrift Encoding    │  to_thrift() → binary
└──────────┬──────────┘
           │
           v
┌─────────────────────┐
│  Zlib Compression   │  compress(level=9) → smaller binary
└──────────┬──────────┘
           │
           v
┌─────────────────────┐
│  MQTT CONNECT       │  MQTToT protocol, client_id=compressed data
└──────────┬──────────┘
           │
           v
┌─────────────────────┐
│  TLS Connection     │  edge-mqtt.facebook.com:443
└──────────┬──────────┘
           │
           v
┌─────────────────────┐
│  Instagram Server   │
└─────────────────────┘

[Incoming Messages]

┌─────────────────────┐
│  Instagram Server   │
└──────────┬──────────┘
           │
           v
┌─────────────────────┐
│  MQTT PUBLISH       │  Topic: "146", Payload: compressed
└──────────┬──────────┘
           │
           v
┌─────────────────────┐
│  Zlib Decompress    │  decompress() → JSON string
└──────────┬──────────┘
           │
           v
┌─────────────────────┐
│  JSON Parse         │  json.loads() → list of IrisPayload
└──────────┬──────────┘
           │
           v
┌─────────────────────┐
│  Extract Data       │  path, value, op, seq_id
└──────────┬──────────┘
           │
           v
┌─────────────────────┐
│  Parse Path         │  /direct_v2/threads/{id}/items/{id}
└──────────┬──────────┘
           │
           v
┌─────────────────────┐
│  Parse Value JSON   │  json.loads(value) → message data
└──────────┬──────────┘
           │
           v
┌─────────────────────┐
│  Final Message      │  {thread_id, text, user_id, ...}
└─────────────────────┘
```

---

This document shows the exact data structures and transformations at every layer of the Instagram MQTT protocol. Use it as a reference when implementing and debugging your port.
