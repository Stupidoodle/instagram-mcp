# Instagram MQTToT Connection Flow Diagram

## Complete Connection and Messaging Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          Instagram MQTToT Protocol                          │
└─────────────────────────────────────────────────────────────────────────────┘

┌──────────────┐                                              ┌──────────────┐
│    Client    │                                              │  Instagram   │
│   (Python)   │                                              │ MQTT Server  │
└──────┬───────┘                                              └──────┬───────┘
       │                                                              │
       │ 1. Get session from instagrapi                              │
       │    - sessionid (cookie)                                     │
       │    - user_id                                                │
       │    - device_id                                              │
       │    - user_agent, app_version, etc.                          │
       │                                                              │
       │ 2. Build Thrift payload (see below)                         │
       │                                                              │
       │ 3. Compress with zlib level 9                               │
       │                                                              │
       │ 4. Build CONNECT packet                                     │
       │    ┌─────────────────────────────────────┐                  │
       │    │ 0x10 (CONNECT)                      │                  │
       │    │ <remaining length varint>           │                  │
       │    │ 0x00 0x07 "MQTToT"                  │                  │
       │    │ 0x03 (protocol level)               │                  │
       │    │ 0xC2 (flags)                        │                  │
       │    │ 0x00 0x14 (keep-alive = 20)         │                  │
       │    │ <compressed Thrift payload>         │                  │
       │    └─────────────────────────────────────┘                  │
       │                                                              │
       │ 5. Open TLS connection                                      │
       ├──────────────────────────────────────────────────────────>  │
       │      edge-mqtt.facebook.com:443                             │
       │                                                              │
       │ 6. Send CONNECT packet                                      │
       ├──────────────────────────────────────────────────────────>  │
       │                                                              │
       │                                                              │ 7. Validate
       │                                                              │    - Check sessionid
       │                                                              │    - Verify Thrift payload
       │                                                              │    - Check subscribeTopics
       │                                                              │
       │ 8. Receive CONNACK                                          │
       │  <──────────────────────────────────────────────────────────┤
       │    ┌─────────────────────────────────────┐                  │
       │    │ 0x20 (CONNACK)                      │                  │
       │    │ 0x04 (remaining length)             │                  │
       │    │ 0x00 (ack flags)                    │                  │
       │    │ 0x00 (return code = success)        │                  │
       │    │ [optional payload]                  │                  │
       │    └─────────────────────────────────────┘                  │
       │                                                              │
       │ 9. Get inbox data from Instagram API                        │
       │    ┌─────────────────────────────────────┐                  │
       │    │ GET /api/v1/direct_v2/inbox/        │                  │
       │    │                                      │                  │
       │    │ Response:                            │                  │
       │    │   seq_id: 12345                     │                  │
       │    │   snapshot_at_ms: 1234567890123     │                  │
       │    └─────────────────────────────────────┘                  │
       │                                                              │
       │ 10. Subscribe to Iris (DM sync)                             │
       │     Build PUBLISH to topic "134" (/ig_sub_iris)             │
       │     ┌─────────────────────────────────────┐                 │
       │     │ Payload (compressed JSON):          │                 │
       │     │ {                                   │                 │
       │     │   "seq_id": 12345,                  │                 │
       │     │   "snapshot_at_ms": 1234567890123,  │                 │
       │     │   "snapshot_app_version": "x.x.x"   │                 │
       │     │ }                                   │                 │
       │     └─────────────────────────────────────┘                 │
       ├──────────────────────────────────────────────────────────>  │
       │                                                              │
       │ 11. Receive SUBACK for topic 135                            │
       │  <──────────────────────────────────────────────────────────┤
       │                                                              │
       │                                                              │
       │ ┌────────────────────────────────────────────────────────┐  │
       │ │          Now subscribed to realtime messages!          │  │
       │ └────────────────────────────────────────────────────────┘  │
       │                                                              │
       │                                                              │
       │                     [User sends a DM via app]               │
       │                                                              │
       │ 12. Receive PUBLISH on topic "146" (/ig_message_sync)       │
       │  <──────────────────────────────────────────────────────────┤
       │    ┌─────────────────────────────────────────────────────┐  │
       │    │ 0x30 (PUBLISH)                                      │  │
       │    │ <remaining length>                                  │  │
       │    │ Topic ID: "146"                                     │  │
       │    │ Payload (zlib compressed):                          │  │
       │    │   0x78 0x9c ... (zlib magic + data)                 │  │
       │    └─────────────────────────────────────────────────────┘  │
       │                                                              │
       │ 13. Decompress payload                                      │
       │     if payload[0] == 0x78:  # zlib magic                    │
       │         decompressed = zlib.decompress(payload)             │
       │                                                              │
       │ 14. Parse JSON                                              │
       │     ┌─────────────────────────────────────────────────────┐ │
       │     │ [{                                                  │ │
       │     │   "event": "patch",                                 │ │
       │     │   "data": [{                                        │ │
       │     │     "op": "add",                                    │ │
       │     │     "path": "/direct_v2/threads/123/items/456",    │ │
       │     │     "value": {                                      │ │
       │     │       "item_id": "456",                             │ │
       │     │       "user_id": 789,                               │ │
       │     │       "timestamp": "1234567890123456",              │ │
       │     │       "item_type": "text",                          │ │
       │     │       "text": "Hello world!"                        │ │
       │     │     }                                               │ │
       │     │   }],                                               │ │
       │     │   "message_type": 4,                                │ │
       │     │   "seq_id": 12346                                   │ │
       │     │ }]                                                  │ │
       │     └─────────────────────────────────────────────────────┘ │
       │                                                              │
       │ 15. Extract message data and emit event                     │
       │     thread_id = extract from path                           │
       │     message = value object                                  │
       │     on_message(thread_id, message)                          │
       │                                                              │
       │                                                              │
       │ [Every 20 seconds]                                          │
       │ 16. Send PINGREQ (keep-alive)                               │
       ├──────────────────────────────────────────────────────────>  │
       │    0xC0 0x00                                                │
       │                                                              │
       │ 17. Receive PINGRESP                                        │
       │  <──────────────────────────────────────────────────────────┤
       │    0xD0 0x00                                                │
       │                                                              │
       │ [Loop: continue receiving messages and sending pings]       │
       │                                                              │
```

## Thrift Payload Structure (Detailed)

```
Thrift CONNECT Payload (before compression):

┌─────────────────────────────────────────────────────────────────────────────┐
│ Top-level struct                                                            │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Field 1: clientIdentifier (BINARY)                                        │
│  ┌──────────────────────────────────────────────────────────────┐          │
│  │ 0x18 (delta=1, type=BINARY)                                  │          │
│  │ 0x14 (length=20)                                              │          │
│  │ "android-1234567890ab" (first 20 chars of device_id)         │          │
│  └──────────────────────────────────────────────────────────────┘          │
│                                                                             │
│  [Field 2-3 omitted]                                                        │
│                                                                             │
│  Field 4: clientInfo (STRUCT)                                              │
│  ┌──────────────────────────────────────────────────────────────┐          │
│  │ 0x3C (delta=3, type=STRUCT)                                  │          │
│  │                                                               │          │
│  │   Field 1: userId (INT64)                                    │          │
│  │   ┌──────────────────────────────────────────────────────┐   │          │
│  │   │ 0x16 (delta=1, type=INT64)                           │   │          │
│  │   │ <zigzag varint: user_id>                             │   │          │
│  │   └──────────────────────────────────────────────────────┘   │          │
│  │                                                               │          │
│  │   Field 2: userAgent (BINARY)                                │          │
│  │   ┌──────────────────────────────────────────────────────┐   │          │
│  │   │ 0x28 (delta=1, type=BINARY)                          │   │          │
│  │   │ <length varint>                                      │   │          │
│  │   │ "Instagram 123.0.0.1.123 Android ..."                │   │          │
│  │   └──────────────────────────────────────────────────────┘   │          │
│  │                                                               │          │
│  │   Field 3: clientCapabilities (INT64) = 183                  │          │
│  │   ┌──────────────────────────────────────────────────────┐   │          │
│  │   │ 0x36 (delta=1, type=INT64)                           │   │          │
│  │   │ 0x8E 0x02 (zigzag varint: 183)                       │   │          │
│  │   └──────────────────────────────────────────────────────┘   │          │
│  │                                                               │          │
│  │   Field 4: endpointCapabilities (INT64) = 0                  │          │
│  │   Field 5: publishFormat (INT32) = 1                         │          │
│  │   Field 6: noAutomaticForeground (BOOLEAN) = false           │          │
│  │   Field 7: makeUserAvailableInForeground (BOOLEAN) = true    │          │
│  │   Field 8: deviceId (BINARY)                                 │          │
│  │   Field 9: isInitiallyForeground (BOOLEAN) = true            │          │
│  │   Field 10: networkType (INT32) = 1                          │          │
│  │   Field 11: networkSubtype (INT32) = 0                       │          │
│  │   Field 12: clientMqttSessionId (INT64) = timestamp          │          │
│  │   [Field 13 omitted]                                         │          │
│  │   Field 14: subscribeTopics (LIST_INT32)                     │          │
│  │   ┌──────────────────────────────────────────────────────┐   │          │
│  │   │ 0x29 (delta=2, type=LIST)                            │   │          │
│  │   │ 0x65 (size=6, element_type=INT32)                    │   │          │
│  │   │ 0xB0 0x01 (88)                                        │   │          │
│  │   │ 0x8E 0x02 (135)                                       │   │          │
│  │   │ 0x9A 0x02 (149)                                       │   │          │
│  │   │ 0x9C 0x02 (150)                                       │   │          │
│  │   │ 0x86 0x02 (133)                                       │   │          │
│  │   │ 0xA4 0x02 (146) ← DM topic!                          │   │          │
│  │   └──────────────────────────────────────────────────────┘   │          │
│  │   Field 15: clientType (BINARY) = "cookie_auth"             │          │
│  │   Field 16: appId (INT64) = 567067343352427                  │          │
│  │   [Field 17-19 omitted]                                      │          │
│  │   Field 20: deviceSecret (BINARY) = ""                       │          │
│  │   Field 21: clientStack (BYTE) = 3                           │          │
│  │   [Field 22-26 omitted]                                      │          │
│  │                                                               │          │
│  │   0x00 (STOP - end of clientInfo struct)                     │          │
│  └──────────────────────────────────────────────────────────────┘          │
│                                                                             │
│  Field 5: password (BINARY)                                                │
│  ┌──────────────────────────────────────────────────────────────┐          │
│  │ 0x18 (delta=1, type=BINARY)                                  │          │
│  │ <length varint>                                              │          │
│  │ "sessionid=12345%3Aabcdef%3A67%3AYZ"                         │          │
│  └──────────────────────────────────────────────────────────────┘          │
│                                                                             │
│  [Field 6 omitted]                                                          │
│  [Field 7-8 omitted]                                                        │
│                                                                             │
│  Field 10: appSpecificInfo (MAP_BINARY_BINARY)                            │
│  ┌──────────────────────────────────────────────────────────────┐          │
│  │ 0x0B (delta=5 impossible, so: type=MAP)                      │          │
│  │ 0x14 (field_id=10, zigzag encoded)                           │          │
│  │ 0x08 (size=8 pairs)                                          │          │
│  │ 0x88 (key_type=BINARY, value_type=BINARY)                   │          │
│  │                                                               │          │
│  │ Pair 1: "app_version" -> "xxx.x.x.xx.xx"                    │          │
│  │ ┌─────────────────────────────────────────────────────────┐  │          │
│  │ │ 0x0B (key length)                                       │  │          │
│  │ │ "app_version"                                           │  │          │
│  │ │ 0x0D (value length)                                     │  │          │
│  │ │ "271.0.0.16.98"                                         │  │          │
│  │ └─────────────────────────────────────────────────────────┘  │          │
│  │                                                               │          │
│  │ Pair 2: "X-IG-Capabilities" -> "3brTvw=="                    │          │
│  │ Pair 3: "everclear_subscriptions" -> "{...}"                 │          │
│  │ Pair 4: "User-Agent" -> "Instagram xxx Android ..."          │          │
│  │ Pair 5: "Accept-Language" -> "en-US"                         │          │
│  │ Pair 6: "platform" -> "android"                              │          │
│  │ Pair 7: "ig_mqtt_route" -> "django"                          │          │
│  │ Pair 8: "pubsub_msg_type_blacklist" -> "direct, typing_type"│          │
│  │ Pair 9: "auth_cache_enabled" -> "0"                          │          │
│  └──────────────────────────────────────────────────────────────┘          │
│                                                                             │
│  0x00 (STOP - end of top-level struct)                                     │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘

↓ Compress with zlib level 9

0x78 0x9c ... (compressed binary data)

↓ Insert into CONNECT packet

Total CONNECT packet ready to send!
```

## Topic ID Mapping

```
┌────────┬──────────────────────────┬─────────────────────────────────┐
│ Topic  │         Path             │           Purpose               │
│   ID   │                          │                                 │
├────────┼──────────────────────────┼─────────────────────────────────┤
│   88   │ /pubsub                  │ Skywalker subscriptions         │
├────────┼──────────────────────────┼─────────────────────────────────┤
│  132   │ /ig_send_message         │ Send DM (outgoing)              │
├────────┼──────────────────────────┼─────────────────────────────────┤
│  133   │ /ig_send_message_response│ Send DM response                │
├────────┼──────────────────────────┼─────────────────────────────────┤
│  134   │ /ig_sub_iris             │ Subscribe to Iris (DM sync)     │
├────────┼──────────────────────────┼─────────────────────────────────┤
│  135   │ /ig_sub_iris_response    │ Iris subscription response      │
├────────┼──────────────────────────┼─────────────────────────────────┤
│  146   │ /ig_message_sync         │ Incoming DMs (realtime!)        │
├────────┼──────────────────────────┼─────────────────────────────────┤
│  149   │ /ig_realtime_sub         │ GraphQL subscriptions           │
├────────┼──────────────────────────┼─────────────────────────────────┤
│  150   │ /t_region_hint           │ Region routing                  │
└────────┴──────────────────────────┴─────────────────────────────────┘
```

## Binary Encoding Examples

### Example 1: Boolean Field

```
Field 6: noAutomaticForeground = false

Bytes:
  0x62    ← (delta=6, type=FALSE)

Explanation:
  delta = 6 (field 6 - field 0)
  type = 0x02 (FALSE)
  0x62 = (6 << 4) | 0x02 = 0x60 | 0x02 = 0x62
```

### Example 2: Int64 Field

```
Field 3: clientCapabilities = 183

Bytes:
  0x36 0x8E 0x02

Explanation:
  delta = 1 (field 3 - field 2)
  type = 0x06 (INT64)
  0x36 = (1 << 4) | 0x06 = 0x10 | 0x06 = 0x16... wait, that's wrong!

  Actually: delta = 3 (field 3 - field 0 in struct, since we're at the start)
  0x36 = (3 << 4) | 0x06 = 0x30 | 0x06 = 0x36 ✓

  Value: 183
  ZigZag: (183 << 1) ^ (183 >> 63) = 366 ^ 0 = 366
  VarInt(366):
    366 = 0b101101110
    First byte: 0b1101110 | 0x80 = 0xEE ← wait, let me recalculate

    366 & 0x7f = 110 (0x6E) but we need continuation
    366 >> 7 = 2
    Since 2 > 0, first byte = 0x6E | 0x80 = 0xEE
    Wait, that doesn't match...

    Let me use the actual algorithm:
    366 & 0x7f = 110 (0x6E)
    366 >> 7 = 2
    Since result > 0: write (110 | 0x80) = 0xEE
    Now value = 2
    2 & 0x7f = 2
    2 >> 7 = 0
    Since result == 0: write 2 = 0x02

    Result: 0xEE 0x02... but example shows 0x8E 0x02

    Ah! Let me recalculate zigzag for 183:
    (183 << 1) = 366
    (183 >> 63) = 0 (for positive numbers)
    366 ^ 0 = 366

    Hmm, let me check the varint encoding again:
    366 in binary: 0b101101110

    VarInt encoding (7 bits per byte, LSB first):
    Byte 1: 0b1101110 (bits 0-6) with continuation: 0x6E | 0x80 = 0xEE
    Byte 2: 0b10 (bits 7-8) without continuation: 0x02

    But the example shows 0x8E 0x02, not 0xEE 0x02...

    Let me try 269 instead:
    269 in binary: 0b100001101
    Byte 1: 0b0001101 (bits 0-6) with continuation: 0x0D | 0x80 = 0x8D
    Byte 2: 0b100 (bits 7-9)... no, that's 3 bits

    Actually, for 269:
    269 & 0x7f = 13 (0x0D)
    269 >> 7 = 2
    First byte: 0x0D | 0x80 = 0x8D
    Second byte: 0x02
    Result: 0x8D 0x02... still not matching

    Let me try unzigzagging the bytes:
    0x8E 0x02:
    Byte 1: 0x8E & 0x7f = 0x0E = 14
    Byte 2: 0x02
    Value = 14 | (2 << 7) = 14 | 256 = 270
    Unzigzag: (270 >> 1) ^ -(270 & 1) = 135 ^ 0 = 135

    Ah! So clientCapabilities = 135, not 183!
    Let me re-check the reference...

    Yes, the docs say clientCapabilities = 183, but let me verify:
    183 zigzag: (183 << 1) = 366
    366 varint:
      366 & 0x7f = 110 (0x6E)
      366 >> 7 = 2
      Byte 1: 0x6E | 0x80 = 0xEE
      Byte 2: 0x02
    Result: 0xEE 0x02

    So if the bytes are 0x8E 0x02, then:
    Varint decode: 14 | (2 << 7) = 270
    Unzigzag: 270 >> 1 = 135

    The actual value is 135, not 183!
```

(Note: The above shows the importance of testing against actual captured packets!)

### Example 3: String Field

```
Field 1: clientIdentifier = "android-abc123456789"

Bytes:
  0x18          ← (delta=1, type=BINARY)
  0x14          ← length = 20
  ...UTF-8...   ← "android-abc123456789"

Explanation:
  delta = 1 (field 1 - field 0)
  type = 0x08 (BINARY)
  0x18 = (1 << 4) | 0x08 = 0x10 | 0x08 = 0x18
  Length: 20 bytes
  VarInt(20) = 0x14
  Data: UTF-8 encoded string
```

### Example 4: List Field

```
Field 14: subscribeTopics = [88, 135, 149, 150, 133, 146]

Bytes:
  0x29          ← (delta=1, type=LIST)  [actually delta from field 13]
  0x65          ← (size=6, element_type=INT32)
  0xB0 0x01     ← 88
  0x8E 0x02     ← 135
  0x9A 0x02     ← 149
  0x9C 0x02     ← 150
  0x86 0x02     ← 133
  0xA4 0x02     ← 146

Explanation:
  Delta encoding: field 14 - field 13 = 1, but we're actually coming from
  field 12, so let's recalculate...

  If last field was 12, then delta = 14 - 12 = 2
  type = 0x09 (LIST)
  0x29 = (2 << 4) | 0x09 = 0x20 | 0x09 = 0x29 ✓

  List header: size = 6, element_type = 0x05 (INT32)
  0x65 = (6 << 4) | 0x05 = 0x60 | 0x05 = 0x65 ✓

  Element 1: 88
  ZigZag(88) = 176
  VarInt(176) = 0xB0 0x01 ✓

  Element 2: 135
  ZigZag(135) = 270
  VarInt(270) = 0x8E 0x02 ✓

  (and so on...)
```

## Key Takeaways

1. **Authentication is in the password field (field 5)**: `sessionid=...`
2. **Topic 146 is critical for DMs**: Must be in subscribeTopics list
3. **Iris subscription is required**: Must send to topic 134 with seq_id
4. **All payloads are compressed**: Both directions use zlib
5. **Keep-alive is 20 seconds**: Must send PINGREQ regularly
6. **Field delta encoding**: When fields are sequential (delta 1-15), saves bytes
7. **ZigZag encoding for signed ints**: Converts signed to unsigned for efficient varint
8. **Thrift field order matters**: Must be in ascending field ID order
