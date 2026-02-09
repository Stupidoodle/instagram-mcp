# Mautrix-Instagram MQTT Analysis - Complete Package

## Overview

This directory contains a complete analysis of the **mautrix-instagram** project's MQTT implementation, which is a production-ready, battle-tested Python implementation of Instagram's realtime messaging protocol.

**Source**: https://github.com/mautrix/instagram
**Analysis Date**: 2026-02-07
**Purpose**: Port working MQTT implementation to instagram-mcp

---

## Document Index

### 1. MAUTRIX_QUICK_REF.md (Quick Reference)
**Size**: 9KB
**Purpose**: Fast lookup for common tasks
**Contains**:
- TL;DR summary
- Critical implementation points
- Key constants
- Common pitfalls
- Quick start template
- Testing checklist

**Use this when**: You need a quick reminder while coding

---

### 2. MAUTRIX_MQTT_ANALYSIS.md (Full Technical Analysis)
**Size**: 28KB
**Purpose**: Deep dive into architecture and implementation
**Contains**:
- Architecture overview
- Complete protocol specification
- MQTToT custom MQTT implementation
- Thrift Compact Protocol details
- Client ID construction
- Topic handling
- Message parsing
- Asyncio integration
- Sequence ID management
- Dependencies
- Porting checklist (8 phases)

**Use this when**: You need to understand HOW and WHY things work

---

### 3. MAUTRIX_CODE_SNIPPETS.md (Ready-to-Port Code)
**Size**: 31KB
**Purpose**: Copy-paste ready code snippets
**Contains**:
- Complete ThriftWriter implementation (181 lines)
- Complete ThriftReader implementation (71 lines)
- MQTToTClient (custom MQTT) (58 lines)
- Topic definitions
- Data structures with thrift_spec
- Client ID builder
- Message handlers
- Path parsing
- Publishing logic
- Sending messages
- Iris subscribe
- Asyncio integration
- Complete minimal example
- Testing snippets

**Use this when**: You're actively porting and need exact code

---

### 4. MAUTRIX_DATA_FLOW.md (Data Examples)
**Size**: 19KB
**Purpose**: See actual data at every layer
**Contains**:
- Connection flow with hex dumps
- Iris subscribe examples
- Receiving messages (6 types)
- Sending messages
- Sequence ID flow
- Complete multi-layer example
- Error scenarios
- Compression examples
- Byte-level Thrift encoding
- Visual data flow diagram

**Use this when**: You need to debug or verify data transformations

---

## Quick Navigation

### I want to understand the overall architecture
→ Start with **MAUTRIX_MQTT_ANALYSIS.md** sections 1-3

### I'm ready to start coding
→ Read **MAUTRIX_QUICK_REF.md**, then copy code from **MAUTRIX_CODE_SNIPPETS.md**

### I need to debug why my packets are wrong
→ Check **MAUTRIX_DATA_FLOW.md** for hex dumps and layer-by-layer examples

### I forgot how to do X
→ Search **MAUTRIX_QUICK_REF.md** first, then full analysis if needed

---

## Implementation Roadmap

### Phase 1: Thrift Foundation (Day 1)
Files to create:
- `instagram_mcp/mqtt/thrift/type.py` (from snippets #1)
- `instagram_mcp/mqtt/thrift/write.py` (from snippets #2)
- `instagram_mcp/mqtt/thrift/read.py` (from snippets #3)

Tests:
- Test zigzag encoding: `123 → 246 (0xf6)`
- Test varint: `300 → [0xac, 0x02]`
- Test simple struct encoding/decoding

References:
- Analysis doc section 3
- Code snippets #1-3
- Data flow section 10

---

### Phase 2: Data Structures (Day 1)
Files to create:
- `instagram_mcp/mqtt/thrift/structures.py`

Implement:
- `RealtimeClientInfo` with `thrift_spec`
- `RealtimeConfig` with `thrift_spec`
- `to_thrift()` method

Tests:
- Build RealtimeConfig object
- Encode to Thrift
- Verify bytes match expected pattern

References:
- Analysis doc section 2
- Code snippets #6
- Data flow section 1

---

### Phase 3: MQTT Client (Day 1-2)
Files to create:
- `instagram_mcp/mqtt/client.py`

Implement:
- `MQTToTClient` extending paho-mqtt
- Override `_send_connect()` with "MQTToT" protocol
- Remove length prefix from client_id

Tests:
- Generate CONNECT packet
- Verify protocol name is "MQTToT"
- Verify client_id has no length prefix

References:
- Analysis doc section 1
- Code snippets #4
- Data flow section 1

---

### Phase 4: Connection (Day 2)
Files to create:
- `instagram_mcp/mqtt/topics.py` (from snippets #5)
- `instagram_mcp/mqtt/connection.py`

Implement:
- Topic enum with encode/decode
- `_form_client_id()` function
- TLS connection to edge-mqtt.facebook.com:443
- Connection event handlers

Tests:
- Connect to Instagram MQTT
- Verify CONNACK code 0
- Verify connection stays alive

References:
- Analysis doc sections 1-2
- Code snippets #5, #7
- Quick ref "Key Constants"

---

### Phase 5: Receiving Messages (Day 2)
Extend `connection.py`:

Implement:
- `_on_message_handler()` with zlib decompress
- `_on_message_sync()` for topic 146
- Path parser `_parse_direct_thread_path()`
- JSON parsing for nested value field

Tests:
- Receive a message sent from Instagram app
- Verify correct thread_id and item_id extracted
- Verify message text is correct

References:
- Analysis doc sections 4-5
- Code snippets #8-9
- Data flow sections 3, 7

---

### Phase 6: Sending Messages (Day 2-3)
Extend `connection.py`:

Implement:
- `publish()` with zlib compression
- `send_text()` function
- Response handler for topic 133
- Client context generation

Tests:
- Send a message
- Verify it appears in Instagram app
- Verify response has correct client_context

References:
- Analysis doc section 7
- Code snippets #10-11
- Data flow section 4

---

### Phase 7: Sequence ID (Day 3)
Extend `connection.py`:

Implement:
- `iris_subscribe()` function
- seq_id tracking
- seq_id persistence to file/db
- Reconnection with saved seq_id

Tests:
- Disconnect and reconnect
- Verify missed messages are received
- Test with expired seq_id (reset to 0)

References:
- Analysis doc section 6
- Code snippets #12
- Data flow section 6

---

### Phase 8: Integration (Day 3)
Integrate with instagram-mcp:

Implement:
- AsyncIO event loop integration
- Event handlers for messages
- Error handling
- Logging

Tests:
- Full end-to-end test
- Send and receive multiple messages
- Test reconnection scenarios
- Test error handling

References:
- Analysis doc section 8
- Code snippets #13-14
- Quick ref testing checklist

---

## Key Insights from Analysis

### 1. Why This is the Best Reference

The mautrix-instagram implementation is:
- **Production-ready**: Used by Matrix bridge users daily
- **Complete**: Handles all edge cases (reconnection, errors, etc.)
- **Well-structured**: Clean separation of concerns
- **Pure Python**: No compiled dependencies except standard libs
- **Actively maintained**: Latest Instagram protocol changes

### 2. Critical Discoveries

1. **MQTToT Protocol**: Instagram uses a custom MQTT variant with:
   - Protocol name "MQTToT" instead of "MQTT"
   - Client ID without length prefix
   - Thrift-encoded connection payload

2. **Double Compression**: ALL payloads (in and out) are zlib-compressed

3. **Double JSON Parsing**: Message content is JSON string inside JSON object

4. **Sequence IDs**: Critical for message recovery after disconnect

5. **Topic Encoding**: Topics are numeric strings ("146") not paths ("/ig_message_sync")

### 3. Common Mistakes to Avoid

1. Using standard MQTT CONNECT (missing "MQTToT")
2. Forgetting to compress outgoing payloads
3. Not parsing nested JSON in message values
4. Not saving sequence IDs
5. Using wrong topic format

### 4. Testing Strategy

1. **Unit tests**: Thrift encoding/decoding
2. **Integration tests**: CONNECT packet formation
3. **Live tests**: Real Instagram connection
4. **Edge cases**: Reconnection, old seq_id, errors

---

## Estimated Effort

- **Thrift implementation**: 4-6 hours
- **MQTT client**: 2-3 hours
- **Connection logic**: 3-4 hours
- **Message handling**: 4-5 hours
- **Testing & debugging**: 4-6 hours

**Total**: 2-3 full days for complete, tested implementation

---

## Files in This Analysis

```
MAUTRIX_README.md           # This file (3KB)
MAUTRIX_QUICK_REF.md        # Quick reference (9KB)
MAUTRIX_MQTT_ANALYSIS.md    # Full technical analysis (28KB)
MAUTRIX_CODE_SNIPPETS.md    # Copy-paste code (31KB)
MAUTRIX_DATA_FLOW.md        # Data examples (19KB)

Total: 90KB of documentation
Source: 89 Python files, ~2000 relevant lines
```

---

## Dependencies

**Required**:
- `paho-mqtt>=1.5,<2` - MQTT client library

**Optional**:
- `attrs>=20.1` - Can use standard dataclasses instead

**Standard library** (included):
- `zlib` - Compression
- `json` - JSON parsing
- `asyncio` - Async/await
- `struct` - Binary packing
- `io` - BytesIO for Thrift

---

## Getting Help

1. **Quick questions**: Check MAUTRIX_QUICK_REF.md
2. **Understanding concepts**: Read MAUTRIX_MQTT_ANALYSIS.md
3. **Implementation details**: Use MAUTRIX_CODE_SNIPPETS.md
4. **Debugging data**: Reference MAUTRIX_DATA_FLOW.md
5. **Original source**: https://github.com/mautrix/instagram

---

## Success Criteria

You'll know the port is working when:

1. ✅ CONNACK returns code 0 (connected)
2. ✅ You receive messages on topic 146 after iris_subscribe
3. ✅ You can parse thread_id and message text from payloads
4. ✅ You can send a message and see it in Instagram app
5. ✅ seq_id increments with each message
6. ✅ Reconnection with saved seq_id recovers missed messages
7. ✅ Connection stays stable for 1+ hour

---

## Notes

- The mautrix-instagram project is AGPL-3.0 licensed
- This analysis is for educational/reference purposes
- When porting, ensure you understand and comply with licenses
- Instagram's unofficial APIs may change without notice
- This implementation follows their mobile app's MQTT protocol

---

**Last Updated**: 2026-02-07
**Analyzed Version**: mautrix-instagram main branch (latest)
**Instagram App Version Reference**: 275.0.0.27.98 (Android)