# MQTToT Quick Reference

## Critical Values

### Connection
- **Host**: `edge-mqtt.facebook.com`
- **Port**: `443` (TLS)
- **Protocol**: `"MQTToT"`
- **Protocol Level**: `3`
- **Connect Flags**: `194` (0xC2)
- **Keep-Alive**: `20` seconds

### Authentication
```python
password_field = f"sessionid={session_id}"  # Field 5 in Thrift
```

### Topic Subscriptions (Field 14 in clientInfo)
```python
subscribe_topics = [88, 135, 149, 150, 133, 146]
```

**Topic 146** = `/ig_message_sync` (DMs!)

### Client Info Values
```python
clientCapabilities = 183
endpointCapabilities = 0
publishFormat = 1
clientType = "cookie_auth"
appId = 567067343352427
clientStack = 3
networkType = 1
networkSubtype = 0
```

## Thrift Schema (Complete)

```python
# Top-level fields:
Field 1: clientIdentifier (BINARY) - device_id[:20]
Field 2: willTopic (BINARY) - unused
Field 3: willMessage (BINARY) - unused
Field 4: clientInfo (STRUCT) - see below
Field 5: password (BINARY) - f"sessionid={session_id}"
Field 6: getDiffsRequests (LIST_BINARY) - unused
Field 9: zeroRatingTokenHash (BINARY) - unused
Field 10: appSpecificInfo (MAP_BINARY_BINARY) - see below

# clientInfo struct (Field 4):
Field 1: userId (INT64)
Field 2: userAgent (BINARY)
Field 3: clientCapabilities (INT64) = 183
Field 4: endpointCapabilities (INT64) = 0
Field 5: publishFormat (INT32) = 1
Field 6: noAutomaticForeground (BOOLEAN) = false
Field 7: makeUserAvailableInForeground (BOOLEAN) = true
Field 8: deviceId (BINARY)
Field 9: isInitiallyForeground (BOOLEAN) = true
Field 10: networkType (INT32) = 1
Field 11: networkSubtype (INT32) = 0
Field 12: clientMqttSessionId (INT64) = int(time.time() * 1000) & 0xffffffff
Field 13: clientIpAddress (BINARY) - unused
Field 14: subscribeTopics (LIST_INT32) = [88, 135, 149, 150, 133, 146]
Field 15: clientType (BINARY) = "cookie_auth"
Field 16: appId (INT64) = 567067343352427
Field 17: overrideNectarLogging (BOOLEAN) - unused
Field 18: connectTokenHash (BINARY) - unused
Field 19: regionPreference (BINARY) - unused
Field 20: deviceSecret (BINARY) = ""
Field 21: clientStack (BYTE) = 3
Field 22: fbnsConnectionKey (INT64) - unused
Field 23: fbnsConnectionSecret (BINARY) - unused
Field 24: fbnsDeviceId (BINARY) - unused
Field 25: fbnsDeviceSecret (BINARY) - unused
Field 26: anotherUnknown (INT64) - unused

# appSpecificInfo map (Field 10):
{
  "app_version": "xxx.x.x.xx.xx",
  "X-IG-Capabilities": "3brTvw==",  # or similar
  "everclear_subscriptions": '{"inapp_notification_subscribe_comment":"17899377895239777","inapp_notification_subscribe_comment_mention_and_reply":"17899377895239777","video_call_participant_state_delivery":"17977239895057311","presence_subscribe":"17846944882223835"}',
  "User-Agent": "Instagram xxx Android (xx/x.x; ...)",
  "Accept-Language": "en-US",
  "platform": "android",
  "ig_mqtt_route": "django",
  "pubsub_msg_type_blacklist": "direct, typing_type",
  "auth_cache_enabled": "0",
}
```

## Thrift Type Constants

```python
STOP = 0x00
TRUE = 0x01
FALSE = 0x02
BYTE = 0x03
INT_16 = 0x04
INT_32 = 0x05
INT_64 = 0x06
DOUBLE = 0x07
BINARY = 0x08
LIST = 0x09
SET = 0x0a
MAP = 0x0b
STRUCT = 0x0c
```

## Encoding Rules

### Field Header
```python
delta = field_id - last_field_id
if 0 < delta <= 15:
    write_byte((delta << 4) | type)
else:
    write_byte(type)
    write_varint(zigzag(field_id))
```

### VarInt
```python
while True:
    byte = value & 0x7f
    value >>= 7
    if value != 0:
        write_byte(byte | 0x80)  # Continuation bit
    else:
        write_byte(byte)
        break
```

### ZigZag
```python
# For int16/int32:
zigzag = (n << 1) ^ (n >> (bits - 1))

# For int64:
zigzag = (n << 1) ^ (n >> 63)
```

### String (BINARY)
```python
utf8 = value.encode('utf-8')
write_varint(len(utf8))
write_bytes(utf8)
```

### List
```python
if size < 15:
    write_byte((size << 4) | element_type)
else:
    write_byte(0xf0 | element_type)
    write_varint(size)
# Then write elements (no field headers)
```

### Map
```python
if size == 0:
    write_byte(0x00)
else:
    write_varint(size)
    write_byte((key_type << 4) | value_type)
    # Then write key-value pairs
```

### Struct
```python
write_field(field_id, STRUCT)
# Save current field counter, reset to 0
# Write nested fields
write_byte(STOP)  # 0x00 ends struct
# Restore field counter
```

### Boolean
```python
# No value byte - type IS the value
write_field(field_id, TRUE if value else FALSE)
```

## CONNECT Packet Format

```
Byte 0: 0x10 (CONNECT packet type)
Bytes 1-N: Remaining length (varint)
Variable header:
  - 0x00 0x07 "MQTToT" (protocol name)
  - 0x03 (protocol level)
  - 0xC2 (connect flags)
  - 0xXX 0xXX (keep-alive, big-endian)
  - <compressed Thrift payload>
```

## CONNACK Packet Format

```
Byte 0: 0x20 (CONNACK packet type)
Byte 1: Remaining length
Byte 2: Ack flags (0-1)
Byte 3: Return code (0-5)
Bytes 4+: Optional payload (length-prefixed)
```

## Compression

```python
# Outgoing (all payloads):
compressed = zlib.compress(data, level=9)

# Incoming (check magic byte):
if data[0] == 0x78:  # Zlib magic
    decompressed = zlib.decompress(data)
else:
    decompressed = data
```

## Iris Subscription

**Must subscribe to receive DMs!**

1. Get inbox data:
```python
inbox = ig_client.direct_inbox()
seq_id = inbox["seq_id"]
snapshot_at_ms = inbox["snapshot_at_ms"]
```

2. Subscribe to topic 134:
```python
sub_data = {
    "seq_id": seq_id,
    "snapshot_at_ms": snapshot_at_ms,
    "snapshot_app_version": app_version,
}
# Publish to topic "134" (/ig_sub_iris)
```

3. Listen for messages on topic 146:
```python
# Topic ID "146" = /ig_message_sync
# Payload is compressed JSON array
```

## Message Format (Topic 146)

```json
[{
  "event": "patch",
  "data": [{
    "op": "add",  // or "replace"
    "path": "/direct_v2/threads/{thread_id}/items/{item_id}",
    "value": {
      "item_id": "123456789012345678",
      "user_id": 123456789,
      "timestamp": "1234567890123456",
      "item_type": "text",
      "text": "Hello world",
      // ... other fields depending on item_type
    }
  }],
  "message_type": 4,
  "seq_id": 12345,
  "mutation_token": "abc123"
}]
```

## Python Implementation Checklist

- [ ] Implement Thrift Compact Protocol writer
  - [ ] Field header encoding with delta compression
  - [ ] VarInt encoding
  - [ ] ZigZag encoding (int16/int32/int64)
  - [ ] String/binary encoding
  - [ ] List encoding
  - [ ] Map encoding
  - [ ] Struct encoding with stack
  - [ ] Boolean encoding (type-as-value)

- [ ] Build MQTToT CONNECT payload
  - [ ] clientIdentifier (field 1)
  - [ ] clientInfo struct (field 4) with all 26 subfields
  - [ ] password with sessionid (field 5)
  - [ ] appSpecificInfo map (field 10)
  - [ ] Compress with zlib level 9

- [ ] Build MQTToT CONNECT packet
  - [ ] Fixed header (0x10)
  - [ ] Remaining length (varint)
  - [ ] Protocol name "MQTToT"
  - [ ] Protocol level 3
  - [ ] Connect flags 194
  - [ ] Keep-alive 20
  - [ ] Compressed payload

- [ ] Parse CONNACK
  - [ ] Ack flags
  - [ ] Return code
  - [ ] Optional payload

- [ ] TLS connection to edge-mqtt.facebook.com:443

- [ ] Implement PUBLISH packet builder

- [ ] Implement PUBLISH packet parser

- [ ] Subscribe to Iris (topic 134)

- [ ] Handle messages from topic 146
  - [ ] Decompress (check 0x78 magic)
  - [ ] Parse JSON
  - [ ] Extract message data

- [ ] Implement keep-alive (PINGREQ/PINGRESP)

## Testing Strategy

1. **Thrift encoder**: Compare output byte-by-byte with TypeScript implementation
2. **CONNECT packet**: Capture with Wireshark/tcpdump
3. **CONNACK**: Verify return code 0
4. **Iris subscription**: Should receive SUBACK on topic 135
5. **Message reception**: Send a DM and verify it arrives on topic 146

## Common Issues

1. **Field order matters**: Thrift fields must be in ascending order
2. **Missing subscribeTopics**: Won't receive messages without topic 146
3. **Wrong compression**: Must be zlib level 9, not gzip
4. **Session expiry**: sessionid must be fresh
5. **Keep-alive timeout**: Must send PINGREQ every 20 seconds
6. **ZigZag encoding**: Don't forget to zigzag int16/int32/int64
7. **Boolean encoding**: Type IS the value (0x01/0x02), no extra byte

## References

- TypeScript implementation: https://github.com/Nerixyz/instagram_mqtt
- Thrift Compact Protocol: https://github.com/apache/thrift/blob/master/doc/specs/thrift-compact-protocol.md
- MQTT 3.1.1 spec: https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/mqtt-v3.1.1.html
- Full analysis: See MQTTOT_PROTOCOL_ANALYSIS.md