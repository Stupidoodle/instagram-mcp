# Instagram MQTT Quick Reference

**Source**: mautrix/instagram (https://github.com/mautrix/instagram)
**Status**: Production-ready, battle-tested implementation

---

## TL;DR - What You Need to Know

1. **Protocol**: MQTToT (MQTT-over-Thrift), NOT standard MQTT
2. **Encoding**: Thrift Compact Protocol
3. **Compression**: zlib level 9 on ALL payloads
4. **Transport**: TLS to edge-mqtt.facebook.com:443
5. **Auth**: Bearer token in Thrift-encoded client_id
6. **Topics**: Numeric strings ("146", "88", etc.)
7. **Format**: JSON payloads after decompression

---

## Critical Implementation Points

### 1. CONNECT Packet is NOT Standard MQTT

```python
# Protocol name is "MQTToT" not "MQTT"
protocol = b"MQTToT"

# Client ID is raw bytes (NO length prefix)
packet.extend(self._client_id)  # Not pack("!H", len(id)) + id
```

### 2. Client ID Structure

```python
client_id = zlib.compress(
    RealtimeConfig(...).to_thrift(),
    level=9
)
```

Must include:
- Authorization Bearer token
- Subscribe topic list: `[88, 135, 244, 149, 150, 245, 274, 133, 146, 179, 34]`
- User ID, device ID, session ID
- App version, capabilities

### 3. All Payloads are Compressed

```python
# Incoming
payload = zlib.decompress(message.payload)

# Outgoing
payload = zlib.compress(json.dumps(data).encode(), level=9)
```

### 4. Message Sync Format (Topic 146)

```python
decompressed = zlib.decompress(payload)
messages = json.loads(decompressed)  # List of IrisPayload

for msg in messages:
    seq_id = msg["seq_id"]  # SAVE THIS!
    for item in msg["data"]:
        op = item["op"]      # "add", "replace", "remove"
        path = item["path"]  # "/direct_v2/threads/{id}/items/{id}"
        value = json.loads(item["value"])  # NESTED JSON!
```

### 5. Sequence ID is Critical

```python
# Save after every message
self.seq_id = message["seq_id"]

# Send on reconnect
iris_subscribe(seq_id=self.seq_id, snapshot_at_ms=timestamp)

# If error "invalid_seq_id", reset to 0
```

---

## File Porting Priority

1. **thrift/type.py** (37 lines) - Type enums
2. **thrift/write.py** (181 lines) - Encoder
3. **thrift/read.py** (71 lines) - Decoder
4. **subscription.py** (377 lines) - Topics
5. **otclient.py** (58 lines) - MQTT client
6. **conn.py** (1014 lines) - Main logic

**Total**: ~1,738 lines to port

---

## Key Constants

```python
# Connection
MQTT_HOST = "edge-mqtt.facebook.com"
MQTT_PORT = 443
MQTT_KEEPALIVE = 60
MQTT_PROTOCOL = 3  # MQTT 3.1

# App
APP_ID = 567067343352427
CLIENT_CAPABILITIES = 0b10110111  # 183

# Topics
TOPIC_MESSAGE_SYNC = "146"          # Incoming messages
TOPIC_SEND_MESSAGE = "132"          # Send messages
TOPIC_SEND_MESSAGE_RESPONSE = "133" # Send response
TOPIC_SUB_IRIS = "134"              # Subscribe request
TOPIC_SUB_IRIS_RESPONSE = "135"     # Subscribe response
```

---

## Path Formats

```python
# New message
"/direct_v2/threads/{thread_id}/items/{item_id}"

# Reaction
"/direct_v2/threads/{thread_id}/items/{item_id}/reactions/{type}/{user_id}"

# Read receipt
"/direct_v2/threads/{thread_id}/participants/{user_id}/has_seen"

# Thread update
"/direct_v2/inbox/threads/{thread_id}"

# Typing
"/direct_v2/threads/{thread_id}/activity_indicator_id/{timestamp}"
```

---

## Thrift Compact Protocol Cheat Sheet

### Integer Encoding (Zigzag + Varint)

```python
# Zigzag: Convert signed to unsigned
zigzag = (val << 1) ^ (val >> (bits - 1))

# Varint: Variable-length encoding
while val:
    byte = val & 0x7F
    if more_bytes:
        byte |= 0x80
    write_byte(byte)
    val >>= 7
```

### Field Header

```python
delta = field_id - prev_field_id

if 0 < delta < 16:
    # Small delta: single byte
    byte = (delta << 4) | type_value
else:
    # Large delta: type + zigzag field_id
    write_byte(type_value)
    write_varint(zigzag(field_id))
```

### Boolean Encoding

```python
# Booleans are encoded as field TYPES, not values
if val:
    write_field(field_id, TType.TRUE)  # Type 1
else:
    write_field(field_id, TType.FALSE)  # Type 2
```

---

## Common Pitfalls

### 1. Forgetting Compression
```python
# ❌ WRONG
payload = json.dumps(data).encode()

# ✅ CORRECT
payload = zlib.compress(json.dumps(data).encode(), level=9)
```

### 2. Double JSON Parsing
```python
# IrisPayload.value is a JSON STRING!
value = item["value"]  # This is a string!
message = json.loads(value)  # Parse it again!
```

### 3. Not Saving seq_id
```python
# ❌ WRONG - No persistence
seq_id = msg["seq_id"]  # Lost on restart

# ✅ CORRECT - Save to file/db
seq_id = msg["seq_id"]
save_to_storage("seq_id", seq_id)
```

### 4. Wrong Protocol Name
```python
# ❌ WRONG
protocol = b"MQTT"

# ✅ CORRECT
protocol = b"MQTToT"
```

### 5. Client ID Length Prefix
```python
# ❌ WRONG (standard MQTT)
packet.extend(struct.pack("!H", len(client_id)))
packet.extend(client_id)

# ✅ CORRECT (MQTToT)
packet.extend(client_id)  # NO length prefix!
```

---

## Testing Checklist

- [ ] Thrift encoding produces correct bytes
- [ ] Thrift decoding reads correctly
- [ ] Client ID compresses to ~500 bytes
- [ ] CONNECT packet has "MQTToT" protocol
- [ ] CONNACK returns code 0 (success)
- [ ] Can subscribe to topic 146
- [ ] Can receive and decompress messages
- [ ] Can parse IrisPayload JSON
- [ ] Can extract message from nested JSON
- [ ] Can send message to topic 132
- [ ] Receive response on topic 133
- [ ] seq_id increments correctly
- [ ] Reconnect with seq_id works
- [ ] Can handle old seq_id error

---

## Debugging Tips

### 1. Verify Thrift Encoding

```python
# Compare with known good output
cfg = RealtimeConfig(...)
thrift_bytes = cfg.to_thrift()

# Check first few bytes
print(f"First 10 bytes: {thrift_bytes[:10].hex()}")
# Should start with field headers
```

### 2. Check Compression

```python
compressed = zlib.compress(data, level=9)
decompressed = zlib.decompress(compressed)

assert decompressed == data
print(f"Compression ratio: {len(compressed)/len(data):.2%}")
```

### 3. Inspect CONNECT Packet

```python
# Capture packet
packet = self._client._packet_queue[0]
print(f"Packet type: {packet[0]:02x}")  # Should be 0x10
print(f"Protocol: {packet[7:13]}")      # Should be b'MQTToT'
print(f"Client ID start: {packet[15:20].hex()}")  # Should be zlib header
```

### 4. Log All Incoming Topics

```python
def on_message(client, userdata, msg):
    print(f"Topic: {msg.topic}")
    print(f"Payload size: {len(msg.payload)}")
    try:
        decompressed = zlib.decompress(msg.payload)
        print(f"Decompressed: {decompressed[:100]}")
    except:
        print("Not compressed or invalid")
```

### 5. Verify seq_id Incrementing

```python
last_seq_id = 0

for msg in messages:
    seq_id = msg["seq_id"]
    assert seq_id > last_seq_id, f"seq_id did not increment: {seq_id} <= {last_seq_id}"
    last_seq_id = seq_id
```

---

## Dependencies

```bash
pip install paho-mqtt>=1.5,<2   # MQTT client
pip install attrs>=20.1         # Dataclasses (optional, can use standard dataclasses)
# Standard library: zlib, json, asyncio, struct, io
```

---

## Quick Start Template

```python
import asyncio
import json
import zlib
import paho.mqtt.client as mqtt

class InstagramMQTT:
    def __init__(self, bearer_token, user_id, device_id):
        # 1. Build client_id (Thrift + zlib)
        client_id = self._build_client_id(bearer_token, user_id, device_id)

        # 2. Create custom MQTT client
        self.client = MQTToTClient(client_id=client_id, clean_session=True)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

        # 3. Enable TLS
        self.client.tls_set()

        # 4. Connect
        self.client.connect("edge-mqtt.facebook.com", 443, 60)

    def _build_client_id(self, bearer_token, user_id, device_id):
        # Build RealtimeConfig -> Thrift -> zlib
        # See MAUTRIX_CODE_SNIPPETS.md for full implementation
        pass

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            print("Connected!")
            # Send iris_subscribe
        else:
            print(f"Connection failed: {rc}")

    def _on_message(self, client, userdata, msg):
        # Decompress
        payload = zlib.decompress(msg.payload)

        # Parse JSON
        data = json.loads(payload)

        # Handle message
        if msg.topic == "146":
            self._handle_message_sync(data)

    def _handle_message_sync(self, data):
        for item in data:
            seq_id = item["seq_id"]
            # Save seq_id!

            for msg in item["data"]:
                path = msg["path"]
                value = json.loads(msg["value"])
                print(f"Message: {value}")

    async def listen(self):
        self.client.loop_start()
        while True:
            await asyncio.sleep(1)

# Usage
mqtt = InstagramMQTT(bearer_token="...", user_id=123, device_id="...")
asyncio.run(mqtt.listen())
```

---

## Resources

1. **MAUTRIX_MQTT_ANALYSIS.md** - Full technical analysis (28KB)
2. **MAUTRIX_CODE_SNIPPETS.md** - Copy-paste code (31KB)
3. **MAUTRIX_DATA_FLOW.md** - Data examples (19KB)
4. **Source**: https://github.com/mautrix/instagram/tree/main/mauigpapi/mqtt

---

## Next Steps

1. Start with Thrift implementation (types, writer, reader)
2. Test Thrift encoding with simple structs
3. Implement MQTToT client (override _send_connect)
4. Test CONNECT with Instagram
5. Implement message handler (decompress + parse)
6. Test receiving messages
7. Implement send_message
8. Test sending messages
9. Implement seq_id persistence
10. Test reconnection

**Estimated effort**: 2-3 days for full implementation with testing
