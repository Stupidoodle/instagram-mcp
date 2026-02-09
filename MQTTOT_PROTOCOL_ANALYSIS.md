# MQTToT Protocol Analysis for Instagram Realtime Messaging

This document provides a complete analysis of the `Nerixyz/instagram_mqtt` TypeScript implementation for porting to Python.

## Table of Contents
1. [Overview](#overview)
2. [MQTToT CONNECT Packet Construction](#mqttot-connect-packet-construction)
3. [Thrift Binary Protocol](#thrift-binary-protocol)
4. [CONNACK Packet Parsing](#connack-packet-parsing)
5. [Realtime Client Connection Flow](#realtime-client-connection-flow)
6. [Message Parsing](#message-parsing)
7. [Python Implementation Guide](#python-implementation-guide)

---

## Overview

**MQTToT** is a modified version of MQTT 3.1.1 used by Instagram/Facebook for realtime messaging. Key differences from standard MQTT:

- **Protocol Name**: `"MQTToT"` instead of `"MQTT"`
- **Protocol Level**: `3` (MQTT 3.1.1)
- **Custom CONNECT Payload**: Uses Thrift-encoded binary payload instead of standard MQTT credentials
- **Extended CONNACK**: Returns additional payload data beyond standard 2-byte response
- **Compression**: All payloads compressed with zlib deflate (level 9)
- **Transport**: TLS on port 443 to `edge-mqtt.facebook.com`

---

## MQTToT CONNECT Packet Construction

### Packet Structure

From `/src/mqttot/mqttot.connect.request.packet.ts`:

```typescript
stream
  .writeString('MQTToT')  // Protocol name (7 bytes: length prefix + "MQTToT")
  .writeByte(3)           // Protocol level
  .writeByte(194)         // Connect flags (0xC2 = 0b11000010)
  .writeWord(keepAlive)   // Keep-alive interval (varint)
  .write(payload)         // Thrift payload (compressed)
```

**Connect Flags Breakdown (194 = 0b11000010)**:
- Bit 7 (User Name Flag): 1
- Bit 6 (Password Flag): 1
- Bit 5 (Will Retain): 0
- Bit 4-3 (Will QoS): 0
- Bit 2 (Will Flag): 0
- Bit 1 (Clean Session): 1
- Bit 0 (Reserved): 0

### Compression

From `/src/shared.ts`:
```typescript
export function compressDeflate(data: string | Buffer): Promise<Buffer> {
   return deflatePromise(data, { level: 9 });
}
```

**Compression**: zlib deflate with level 9 (maximum compression)

### Connection Parameters

From `/src/realtime/realtime.client.ts` (lines 67-111):

```typescript
const connection = new MQTToTConnection({
  clientIdentifier: deviceId.substring(0, 20),  // First 20 chars of phoneId
  clientInfo: {
    userId: BigInt(Number(this.ig.state.cookieUserId)),
    userAgent: this.ig.state.appUserAgent,
    clientCapabilities: 183,
    endpointCapabilities: 0,
    publishFormat: 1,
    noAutomaticForeground: false,
    makeUserAvailableInForeground: true,
    deviceId: deviceId,
    isInitiallyForeground: true,
    networkType: 1,
    networkSubtype: 0,
    clientMqttSessionId: BigInt(Date.now()) & BigInt(0xffffffff),
    subscribeTopics: [88, 135, 149, 150, 133, 146],  // Critical!
    clientType: 'cookie_auth',
    appId: BigInt(567067343352427),
    deviceSecret: '',
    clientStack: 3,
  },
  password: `sessionid=${sessionid}`,  // Authentication!
  appSpecificInfo: {
    app_version: this.ig.state.appVersion,
    'X-IG-Capabilities': this.ig.state.capabilitiesHeader,
    everclear_subscriptions: JSON.stringify({
      inapp_notification_subscribe_comment: '17899377895239777',
      inapp_notification_subscribe_comment_mention_and_reply: '17899377895239777',
      video_call_participant_state_delivery: '17977239895057311',
      presence_subscribe: '17846944882223835',
    }),
    'User-Agent': userAgent,
    'Accept-Language': this.ig.state.language.replace('_', '-'),
    platform: 'android',
    ig_mqtt_route: 'django',
    pubsub_msg_type_blacklist: 'direct, typing_type',
    auth_cache_enabled: '0',
  },
})
```

**Key Values**:
- **subscribeTopics**: `[88, 135, 149, 150, 133, 146]`
  - 88 = `/pubsub`
  - 135 = `/ig_sub_iris_response`
  - 149 = `/ig_realtime_sub`
  - 150 = `/t_region_hint`
  - 133 = `/ig_send_message_response`
  - 146 = `/ig_message_sync` (DMs!)

- **clientCapabilities**: 183
- **endpointCapabilities**: 0
- **publishFormat**: 1
- **clientStack**: 3
- **appId**: 567067343352427 (Instagram)
- **clientType**: `"cookie_auth"`

---

## Thrift Binary Protocol

### Thrift Type System

From `/src/thrift/thrift.ts`:

```typescript
export const ThriftTypes = {
  STOP: 0x00,
  TRUE: 0x01,
  FALSE: 0x02,
  BYTE: 0x03,
  INT_16: 0x04,
  INT_32: 0x05,
  INT_64: 0x06,
  DOUBLE: 0x07,
  BINARY: 0x08,
  LIST: 0x09,
  SET: 0x0a,
  MAP: 0x0b,
  STRUCT: 0x0c,

  // Compound types (element type << 8 | container type)
  LIST_INT_16: (0x04 << 8) | 0x09,
  LIST_INT_32: (0x05 << 8) | 0x09,
  LIST_INT_64: (0x06 << 8) | 0x09,
  LIST_BINARY: (0x08 << 8) | 0x09,
  MAP_BINARY_BINARY: (0x88 << 8) | 0x0b,
};
```

### CONNECT Payload Thrift Schema

From `/src/mqttot/mqttot.connection.ts` (lines 56-94):

**Complete Thrift descriptor array**:

```typescript
[
  // Field 1: clientIdentifier (binary string)
  ThriftDescriptors.binary('clientIdentifier', 1),

  // Field 2: willTopic (binary string)
  ThriftDescriptors.binary('willTopic', 2),

  // Field 3: willMessage (binary string)
  ThriftDescriptors.binary('willMessage', 3),

  // Field 4: clientInfo (nested struct)
  ThriftDescriptors.struct('clientInfo', 4, [
    ThriftDescriptors.int64('userId', 1),
    ThriftDescriptors.binary('userAgent', 2),
    ThriftDescriptors.int64('clientCapabilities', 3),
    ThriftDescriptors.int64('endpointCapabilities', 4),
    ThriftDescriptors.int32('publishFormat', 5),
    ThriftDescriptors.boolean('noAutomaticForeground', 6),
    ThriftDescriptors.boolean('makeUserAvailableInForeground', 7),
    ThriftDescriptors.binary('deviceId', 8),
    ThriftDescriptors.boolean('isInitiallyForeground', 9),
    ThriftDescriptors.int32('networkType', 10),
    ThriftDescriptors.int32('networkSubtype', 11),
    ThriftDescriptors.int64('clientMqttSessionId', 12),
    ThriftDescriptors.binary('clientIpAddress', 13),
    ThriftDescriptors.listOfInt32('subscribeTopics', 14),
    ThriftDescriptors.binary('clientType', 15),
    ThriftDescriptors.int64('appId', 16),
    ThriftDescriptors.boolean('overrideNectarLogging', 17),
    ThriftDescriptors.binary('connectTokenHash', 18),
    ThriftDescriptors.binary('regionPreference', 19),
    ThriftDescriptors.binary('deviceSecret', 20),
    ThriftDescriptors.byte('clientStack', 21),
    ThriftDescriptors.int64('fbnsConnectionKey', 22),
    ThriftDescriptors.binary('fbnsConnectionSecret', 23),
    ThriftDescriptors.binary('fbnsDeviceId', 24),
    ThriftDescriptors.binary('fbnsDeviceSecret', 25),
    ThriftDescriptors.int64('anotherUnknown', 26),
  ]),

  // Field 5: password (binary string) - SESSION AUTH!
  ThriftDescriptors.binary('password', 5),

  // Field 5 (alt): unknown (int16) - polyfill
  ThriftDescriptors.int16('unknown', 5),

  // Field 6: getDiffsRequests (list of binary)
  ThriftDescriptors.listOfBinary('getDiffsRequests', 6),

  // Field 9: zeroRatingTokenHash (binary string)
  ThriftDescriptors.binary('zeroRatingTokenHash', 9),

  // Field 10: appSpecificInfo (map of binary -> binary)
  ThriftDescriptors.mapBinaryBinary('appSpecificInfo', 10),
]
```

### Thrift Encoding Details

#### Field Header Encoding

From `/src/thrift/thrift.writing.ts` (lines 185-196):

```typescript
private writeField(field: number, type: number): this {
  const delta = field - this.field;
  if (delta > 0 && delta <= 15) {
    this.writeByte((delta << 4) | type);  // Delta encoding
  } else {
    this.writeByte(type);
    this.writeWord(field);  // Full field ID
  }
  this._field = field;
  return this;
}
```

**Field encoding**:
- If field delta is 1-15: Single byte = `(delta << 4) | type`
- Otherwise: Type byte + field ID (varint zigzag)

#### VarInt Encoding

```typescript
private writeVarInt(num: number): this {
  while (true) {
    let byte = num & ~0x7f;
    if (byte === 0) {
      this.writeByte(num);
      break;
    } else if (byte === -128) {
      this.writeByte(0);
      break;
    } else {
      byte = (num & 0xff) | 0x80;
      this.writeByte(byte);
      num = num >> 7;
    }
  }
  return this;
}
```

**VarInt**: 7 bits per byte, MSB = continuation bit

#### ZigZag Encoding

```typescript
public static toZigZag = (n: number, bits: number) => (n << 1) ^ (n >> (bits - 1));
public static bigintToZigZag(n: bigint): bigint {
  return (n << BigInt(1)) ^ (n >> BigInt(63));
}
```

**ZigZag**: Maps signed integers to unsigned for efficient varint encoding
- Positive: `n * 2`
- Negative: `abs(n) * 2 - 1`

#### Type-Specific Encoding

**Boolean** (lines 253-254):
```typescript
public writeBoolean(field: number, bool: boolean): this {
  return this.writeField(field, bool ? ThriftTypes.TRUE : ThriftTypes.FALSE);
}
```
No value byte - the type itself encodes the boolean!

**String/Binary** (lines 257-267):
```typescript
public writeString(field: number, s: string): this {
  this.writeField(field, ThriftTypes.BINARY);
  return this.writeStringDirect(s);
}

public writeStringDirect(s: string): this {
  const buf = Buffer.from(s, 'utf8');
  this.writeVarInt(buf.length);
  this.writeBuffer(buf);
  return this;
}
```
Format: Field header + length (varint) + UTF-8 bytes

**Int16/Int32** (lines 282-290):
```typescript
public writeInt16(field: number, num: number): this {
  this.writeField(field, ThriftTypes.INT_16);
  return this.writeWord(num);
}

public writeInt32(field: number, num: number): this {
  this.writeField(field, ThriftTypes.INT_32);
  return this.writeInt(num);
}

private writeWord(num: number): this {
  return this.writeVarInt(BufferWriter.toZigZag(num, 0x10));
}

private writeInt(num: number): this {
  return this.writeVarInt(BufferWriter.toZigZag(num, 0x20));
}
```
Format: Field header + zigzag varint

**Int64** (lines 292-295):
```typescript
public writeInt64Buffer(field: number, num: Int64): this {
  this.writeField(field, ThriftTypes.INT_64);
  return this.writeLong(num);
}

private writeLong(num: Int64 | { int: Int64; num: number }): this {
  if (typeof num === 'object') {
    num = num.int;
  }
  if (typeof num !== 'bigint') {
    num = BigInt(num);
  }
  this.writeBigint(BufferWriter.bigintToZigZag(num));
  return this;
}
```
Format: Field header + zigzag varint (bigint)

**List** (lines 297-343):
```typescript
public writeList(field: number, type: number, list: []): this {
  this.writeField(field, ThriftTypes.LIST);
  const size = list.length;

  if (size < 0x0f) {
    this.writeByte((size << 4) | type);  // Compact: size in upper 4 bits
  } else {
    this.writeByte(0xf0 | type);         // Full: 0xF marker + varint size
    this.writeVarInt(size);
  }

  switch (type) {
    case ThriftTypes.BYTE:
      list.forEach(el => this.writeByte(el));
      break;
    case ThriftTypes.INT_16:
      list.forEach(el => this.writeWord(el));
      break;
    case ThriftTypes.INT_32:
      list.forEach(el => this.writeInt(el));
      break;
    case ThriftTypes.BINARY:
      list.forEach(el => {
        const buf = Buffer.from(el, 'utf8');
        this.writeVarInt(buf.length);
        this.writeBuffer(buf);
      });
      break;
  }
  return this;
}
```
Format:
- Field header
- If size < 15: `(size << 4) | element_type`
- If size >= 15: `0xF0 | element_type` + size (varint)
- Elements (no field headers, just values)

**Map** (lines 242-251, 87-106):
```typescript
public writeMapHeader(field: number, size: number, keyType: number, valueType: number): this {
  this.writeField(field, ThriftTypes.MAP);
  if (size === 0) {
    this.writeByte(0);
  } else {
    this.writeVarInt(size);
    this.writeByte(((keyType & 0xf) << 4) | (valueType & 0xf));
  }
  return this;
}

// Usage for MAP_BINARY_BINARY:
if (descriptor.type === ThriftTypes.MAP_BINARY_BINARY) {
  let pairs: [string, string][];
  if (Array.isArray(value)) {
    pairs = value.map((x: { key: string; value: string }) => [x.key, x.value]);
  } else {
    pairs = Object.entries(value);
  }
  writer.writeMapHeader(descriptor.field, pairs.length, ThriftTypes.BINARY, ThriftTypes.BINARY);
  if (pairs.length !== 0) {
    for (const pair of pairs) {
      writer.writeStringDirect(pair[0]).writeStringDirect(pair[1]);
    }
  }
}
```
Format:
- Field header
- If size = 0: Single 0x00 byte
- If size > 0: size (varint) + `(key_type << 4) | value_type` + pairs

**Struct** (lines 72-76, 345-358):
```typescript
public writeStruct(field: number): this {
  this.writeField(field, ThriftTypes.STRUCT);
  this.pushStack();
  return this;
}

public pushStack() {
  this._stack.push(this.field);
  this._field = 0;  // Reset field counter for nested context
}

public popStack() {
  this._field = this._stack.pop() ?? -1;
}

// Usage:
writer.writeStruct(descriptor.field);
thriftWriteSingleLayerFromObject(value, descriptor.structDescriptors ?? [], writer);
writer.writeStop();  // STOP marker (0x00) ends struct
```
Format:
- Field header
- Nested fields (field counter resets to 0)
- STOP byte (0x00)

---

## CONNACK Packet Parsing

From `/src/mqttot/mqttot.connect.response.packet.ts`:

```typescript
export function readConnectResponsePacket(stream: PacketStream, remaining: number): MQTToTConnectResponsePacket {
  const ack = stream.readByte();
  const returnCode = stream.readByte();
  if (ack > 1) {
    throw new Error('Invalid ack');
  } else if (returnCode > 5) {
    throw new Error('Invalid return code');
  }
  return new MQTToTConnectResponsePacket(
    ack,
    returnCode as ConnectReturnCode,
    remaining > 2 ? stream.readStringAsBuffer() : Buffer.alloc(0),  // Extra payload!
  );
}
```

**Standard MQTT CONNACK**: 2 bytes (ack flags + return code)

**MQTToT CONNACK**: 2 bytes + optional payload
- Byte 1: Acknowledgment flags (0-1)
- Byte 2: Return code (0-5)
- Remaining bytes: Payload (length-prefixed string/buffer)

**Return codes** (standard MQTT):
- 0: Connection Accepted
- 1: Unacceptable Protocol Version
- 2: Identifier Rejected
- 3: Server Unavailable
- 4: Bad Username or Password
- 5: Not Authorized

From `/src/mqttot/mqttot.client.ts` (lines 117-138):

```typescript
export function mqttotConnectFlow(
  payload: Buffer,
  requirePayload: boolean,
): PacketFlowFunc<MQTToTReadMap, MQTToTWriteMap, MQTToTConnectResponsePacket> {
  return (success, error) => ({
    start: () => ({
      type: PacketType.Connect,
      options: {
        payload,
        keepAlive: 60,  // 60 seconds
      },
    }),
    accept: isConnAck,
    next: (packet: MQTToTConnectResponsePacket) => {
      if (packet.isSuccess) {
        if (packet.payload?.length || !requirePayload) success(packet);
        else error(new EmptyPacketError(`CONNACK: no payload (payloadExpected): ${packet.payload}`));
      } else
        error(new ConnectionFailedError(`CONNACK returnCode: ${packet.returnCode} errorName: ${packet.errorName}`));
    },
  });
}
```

**For Instagram Realtime**: `requirePayload: false` (line 129 in `realtime.client.ts`)

---

## Realtime Client Connection Flow

From `/src/realtime/realtime.client.ts`:

### 1. Connect Setup

```typescript
public async connect(initOptions?: RealtimeClientInitOptions | string[]): Promise<any> {
  this._mqtt = new MQTToTClient({
    url: 'edge-mqtt.facebook.com',  // REALTIME.HOST_NAME_V6
    payloadProvider: () => {
      this.constructConnection();
      if (!this.connection) {
        throw new IllegalStateError("constructConnection() didn't create a connection");
      }
      return compressDeflate(this.connection.toThrift());  // Compress Thrift payload
    },
    enableTrace: this.initOptions?.enableTrace,
    autoReconnect: this.initOptions?.autoReconnect ?? true,
    requirePayload: false,
    socksOptions: this.initOptions?.socksOptions,
    additionalOptions: this.initOptions?.additionalTlsOptions,
  });

  // Set up message handler
  this.mqtt!.on('message', async msg => {
    const unzipped = await tryUnzipAsync(msg.payload);  // Decompress
    const topic = RealtimeTopicsArray.find(t => t.id === msg.topic);
    if (topic && topic.parser && !topic.noParse) {
      const parsedMessages = topic.parser.parseMessage(topic, unzipped);
      this.emit('receive', topic, Array.isArray(parsedMessages) ? parsedMessages : [parsedMessages]);
    } else {
      this.emit('receiveRaw', msg);
    }
  });

  return new Promise((resolve, reject) => {
    this.mqtt!.on('connect', async () => {
      const { graphQlSubs, skywalkerSubs, irisData } = this.initOptions;
      await Promise.all([
        graphQlSubs && graphQlSubs.length > 0 ? this.graphQlSubscribe(graphQlSubs) : null,
        skywalkerSubs && skywalkerSubs.length > 0 ? this.skywalkerSubscribe(skywalkerSubs) : null,
        irisData ? this.irisSubscribe(irisData) : null,
      ]).then(resolve);
    });
    this.mqtt!.connect({
      keepAlive: 20,      // 20 seconds for realtime
      protocolLevel: 3,
      clean: true,
      connectDelay: 60 * 1000,  // 60s reconnect delay
    }).catch(e => {
      this.emitError(e);
      reject(e);
    });
  });
}
```

### 2. Topic Subscriptions

**GraphQL Subscription** (lines 196-208):
```typescript
public graphQlSubscribe(sub: string | string[]): Promise<MqttMessageOutgoing> {
  sub = typeof sub === 'string' ? [sub] : sub;
  return this.commands.updateSubscriptions({
    topic: Topics.REALTIME_SUB,  // Topic ID 149
    data: {
      sub,  // Array of subscription IDs
    },
  });
}
```

**Iris Subscription** (lines 224-243) - **CRITICAL FOR DMs**:
```typescript
public irisSubscribe({
  seq_id,
  snapshot_at_ms,
}: {
  seq_id: number;
  snapshot_at_ms: number;
}): Promise<MqttMessageOutgoing> {
  return this.commands.updateSubscriptions({
    topic: Topics.IRIS_SUB,  // Topic ID 134
    data: {
      seq_id,               // Sequence ID from inbox
      snapshot_at_ms,       // Snapshot timestamp from inbox
      snapshot_app_version: this.ig.state.appVersion,
    },
  });
}
```

**Getting Iris data** (from example):
```typescript
irisData: await ig.feed.directInbox().request()
```

This returns an object with `seq_id` and `snapshot_at_ms` fields from the inbox API.

### 3. Critical Topics

From `/src/constants.ts`:

```typescript
export const Topics = {
  MESSAGE_SYNC: {
    id: '146',
    path: '/ig_message_sync',
    parser: new IrisParser(),
    noParse: true,  // Manual parsing in mixin
  },
  IRIS_SUB: {
    id: '134',
    path: '/ig_sub_iris',
    parser: null,
  },
  IRIS_SUB_RESPONSE: {
    id: '135',
    path: '/ig_sub_iris_response',
    parser: new JsonParser(),
  },
  SEND_MESSAGE: {
    id: '132',
    path: '/ig_send_message',
    parser: null,
  },
  SEND_MESSAGE_RESPONSE: {
    id: '133',
    path: '/ig_send_message_response',
    parser: new JsonParser(),
  },
  REALTIME_SUB: {
    id: '149',
    path: '/ig_realtime_sub',
    parser: new GraphqlParser(),
    noParse: true,
  },
};
```

---

## Message Parsing

### Topic 146: `/ig_message_sync` (DMs)

From `/src/realtime/parsers/iris.parser.ts`:

```typescript
export class IrisParser extends Parser<IrisParserData, ParsedMessage<IrisParserData>> {
  parseMessage(topic: Topic, payload: Buffer): ParsedMessage<IrisParserData>[] {
    return JSON.parse(payload.toString('utf8')).map((x: IrisParserData) => ({
      topic,
      data: x,
    }));
  }
}
```

**Format**: Compressed JSON array

**Decompression** (from `/src/shared.ts`):
```typescript
export async function tryUnzipAsync(data: Buffer): Promise<Buffer> {
  try {
    if (data.readInt8(0) !== 0x78) return data;  // Check zlib magic byte
    return unzipAsync(data);
  } catch (e) {
    return data;
  }
}
```

### Message Handling

From `/src/realtime/mixins/message-sync.mixin.ts`:

```typescript
export class MessageSyncMixin extends Mixin {
  private handleMessageSync(data: IrisParserData) {
    if (!data.data) {
      this.client.emit('iris', data);
      return;
    }

    const threadIdMatch = data.path?.match(/^\/direct_v2\/(inbox\/)?threads\/([^/]+)/);
    const threadId = threadIdMatch?.[2];

    if (data.path?.startsWith('/direct_v2/threads')) {
      this.client.emit('message', {
        threadId,
        message: data.data,
        op: data.op,
      });
    } else {
      this.client.emit('threadUpdate', {
        threadId,
        op: data.op,
        path: data.path,
        data: data.data,
      });
    }
  }
}
```

**Message structure** (from `/src/realtime/messages/message-sync.message.ts`):

```typescript
export interface MessageSyncMessage {
  thread_id: string;
  thread_v2_id: string;
  item_id: string;
  user_id: number;
  timestamp: string;
  op: 'add' | 'replace';
  path: string;
  item_type: string;  // 'text', 'media', 'reel_share', 'action_log', etc.

  // Depends on item_type:
  text?: string;
  media?: RegularMediaItem;
  voice_media?: VoiceMediaItem;
  animated_media?: AnimatedMediaItem;
  reactions?: {
    likes: any[];
    emojis: any[];
  };
  // ... many more type-specific fields
}
```

### Connection Lifecycle

1. **Construct Thrift CONNECT payload** with session auth
2. **Compress** with zlib deflate level 9
3. **Send CONNECT** packet to `edge-mqtt.facebook.com:443`
4. **Receive CONNACK** (may include payload)
5. **Subscribe to Iris** with `seq_id` and `snapshot_at_ms` from inbox API
6. **Receive messages** on topic 146 (`/ig_message_sync`)
7. **Decompress** incoming messages (zlib)
8. **Parse JSON** to get message data
9. **Emit events** based on message type and path

---

## Python Implementation Guide

### Required Libraries

```python
# MQTT
paho-mqtt  # Base MQTT client (modify for MQTToT)

# Thrift (manual implementation needed)
# No library - implement compact protocol manually

# Compression
zlib  # Built-in

# Instagram API
instagrapi  # For session/cookie management
```

### Implementation Steps

#### 1. Thrift Encoder

```python
class ThriftCompactWriter:
    """
    Implements Thrift Compact Protocol binary encoding.
    Based on Nerixyz/instagram_mqtt implementation.
    """

    def __init__(self):
        self.buffer = bytearray()
        self.field = 0
        self.stack = []

    def write_field(self, field_id: int, field_type: int):
        """Write field header with delta encoding."""
        delta = field_id - self.field
        if 0 < delta <= 15:
            self.buffer.append((delta << 4) | field_type)
        else:
            self.buffer.append(field_type)
            self.write_varint(self.zigzag_encode(field_id, 16))
        self.field = field_id

    def write_varint(self, value: int):
        """Write variable-length integer."""
        while True:
            byte = value & ~0x7f
            if byte == 0:
                self.buffer.append(value)
                break
            elif byte == -128:
                self.buffer.append(0)
                break
            else:
                self.buffer.append((value & 0xff) | 0x80)
                value >>= 7

    @staticmethod
    def zigzag_encode(n: int, bits: int) -> int:
        """ZigZag encoding for signed integers."""
        return (n << 1) ^ (n >> (bits - 1))

    def write_bool(self, field_id: int, value: bool):
        """Write boolean (type encodes value)."""
        self.write_field(field_id, 0x01 if value else 0x02)

    def write_byte(self, field_id: int, value: int):
        """Write signed byte."""
        self.write_field(field_id, 0x03)
        self.buffer.append(value & 0xff)

    def write_i16(self, field_id: int, value: int):
        """Write 16-bit integer."""
        self.write_field(field_id, 0x04)
        self.write_varint(self.zigzag_encode(value, 16))

    def write_i32(self, field_id: int, value: int):
        """Write 32-bit integer."""
        self.write_field(field_id, 0x05)
        self.write_varint(self.zigzag_encode(value, 32))

    def write_i64(self, field_id: int, value: int):
        """Write 64-bit integer."""
        self.write_field(field_id, 0x06)
        # Use bigint zigzag
        zigzag = (value << 1) ^ (value >> 63)
        self.write_varint_i64(zigzag)

    def write_varint_i64(self, value: int):
        """Write 64-bit varint."""
        while True:
            if (value & ~0x7f) == 0:
                self.buffer.append(value)
                break
            else:
                self.buffer.append((value & 0x7f) | 0x80)
                value >>= 7

    def write_string(self, field_id: int, value: str):
        """Write UTF-8 string."""
        self.write_field(field_id, 0x08)  # BINARY
        utf8 = value.encode('utf-8')
        self.write_varint(len(utf8))
        self.buffer.extend(utf8)

    def write_list_i32(self, field_id: int, values: list[int]):
        """Write list of int32."""
        self.write_field(field_id, 0x09)  # LIST
        size = len(values)
        if size < 15:
            self.buffer.append((size << 4) | 0x05)  # INT_32
        else:
            self.buffer.append(0xf0 | 0x05)
            self.write_varint(size)
        for val in values:
            self.write_varint(self.zigzag_encode(val, 32))

    def write_map_string_string(self, field_id: int, pairs: dict[str, str]):
        """Write map of string -> string."""
        self.write_field(field_id, 0x0b)  # MAP
        size = len(pairs)
        if size == 0:
            self.buffer.append(0)
        else:
            self.write_varint(size)
            self.buffer.append(0x88)  # (BINARY << 4) | BINARY
            for key, value in pairs.items():
                # Write key
                utf8_key = key.encode('utf-8')
                self.write_varint(len(utf8_key))
                self.buffer.extend(utf8_key)
                # Write value
                utf8_val = value.encode('utf-8')
                self.write_varint(len(utf8_val))
                self.buffer.extend(utf8_val)

    def write_struct_begin(self, field_id: int):
        """Begin nested struct."""
        self.write_field(field_id, 0x0c)  # STRUCT
        self.stack.append(self.field)
        self.field = 0

    def write_struct_end(self):
        """End nested struct."""
        self.buffer.append(0x00)  # STOP
        if self.stack:
            self.field = self.stack.pop()

    def write_stop(self):
        """Write STOP marker."""
        self.buffer.append(0x00)

    def get_bytes(self) -> bytes:
        """Get final bytes."""
        return bytes(self.buffer)
```

#### 2. MQTToT Connection Payload

```python
import zlib
from typing import Optional

def build_mqttot_connect_payload(
    user_id: int,
    session_id: str,
    device_id: str,
    user_agent: str,
    app_version: str,
    capabilities_header: str,
    language: str = "en_US",
) -> bytes:
    """
    Build Instagram MQTToT CONNECT payload.

    Returns compressed Thrift binary payload.
    """
    writer = ThriftCompactWriter()

    # Field 1: clientIdentifier (first 20 chars of device_id)
    writer.write_string(1, device_id[:20])

    # Field 4: clientInfo (nested struct)
    writer.write_struct_begin(4)

    # clientInfo fields
    writer.write_i64(1, user_id)  # userId
    writer.write_string(2, user_agent)  # userAgent
    writer.write_i64(3, 183)  # clientCapabilities
    writer.write_i64(4, 0)  # endpointCapabilities
    writer.write_i32(5, 1)  # publishFormat
    writer.write_bool(6, False)  # noAutomaticForeground
    writer.write_bool(7, True)  # makeUserAvailableInForeground
    writer.write_string(8, device_id)  # deviceId
    writer.write_bool(9, True)  # isInitiallyForeground
    writer.write_i32(10, 1)  # networkType
    writer.write_i32(11, 0)  # networkSubtype

    # clientMqttSessionId (current timestamp & 0xffffffff)
    import time
    session_id_int = int(time.time() * 1000) & 0xffffffff
    writer.write_i64(12, session_id_int)

    # subscribeTopics: [88, 135, 149, 150, 133, 146]
    writer.write_list_i32(14, [88, 135, 149, 150, 133, 146])

    writer.write_string(15, "cookie_auth")  # clientType
    writer.write_i64(16, 567067343352427)  # appId
    writer.write_string(20, "")  # deviceSecret
    writer.write_byte(21, 3)  # clientStack

    writer.write_struct_end()  # End clientInfo

    # Field 5: password (session authentication!)
    writer.write_string(5, f"sessionid={session_id}")

    # Field 10: appSpecificInfo (map)
    app_info = {
        "app_version": app_version,
        "X-IG-Capabilities": capabilities_header,
        "everclear_subscriptions": json.dumps({
            "inapp_notification_subscribe_comment": "17899377895239777",
            "inapp_notification_subscribe_comment_mention_and_reply": "17899377895239777",
            "video_call_participant_state_delivery": "17977239895057311",
            "presence_subscribe": "17846944882223835",
        }),
        "User-Agent": user_agent,
        "Accept-Language": language.replace("_", "-"),
        "platform": "android",
        "ig_mqtt_route": "django",
        "pubsub_msg_type_blacklist": "direct, typing_type",
        "auth_cache_enabled": "0",
    }
    writer.write_map_string_string(10, app_info)

    writer.write_stop()  # End top-level struct

    # Compress with zlib level 9
    thrift_bytes = writer.get_bytes()
    compressed = zlib.compress(thrift_bytes, level=9)

    return compressed
```

#### 3. MQTToT CONNECT Packet

```python
def build_mqttot_connect_packet(payload: bytes, keep_alive: int = 20) -> bytes:
    """
    Build MQTToT CONNECT packet.

    Returns raw bytes to send over TLS socket.
    """
    packet = bytearray()

    # Fixed header: packet type (CONNECT = 0x10)
    packet.append(0x10)

    # Variable header + payload
    var_header = bytearray()

    # Protocol name: "MQTToT"
    protocol_name = b"MQTToT"
    var_header.append(0x00)
    var_header.append(len(protocol_name))
    var_header.extend(protocol_name)

    # Protocol level: 3
    var_header.append(3)

    # Connect flags: 194 (0xC2)
    var_header.append(194)

    # Keep alive: 16-bit big-endian
    var_header.append((keep_alive >> 8) & 0xff)
    var_header.append(keep_alive & 0xff)

    # Payload (compressed Thrift)
    var_header.extend(payload)

    # Remaining length (varint encoding)
    remaining_length = len(var_header)
    while True:
        byte = remaining_length % 128
        remaining_length //= 128
        if remaining_length > 0:
            packet.append(byte | 0x80)
        else:
            packet.append(byte)
            break

    packet.extend(var_header)

    return bytes(packet)
```

#### 4. CONNACK Parsing

```python
def parse_mqttot_connack(data: bytes) -> tuple[int, int, bytes]:
    """
    Parse MQTToT CONNACK packet.

    Returns (ack_flags, return_code, payload).
    """
    # Skip fixed header (assume already parsed)
    pos = 2  # After packet type + remaining length

    ack_flags = data[pos]
    pos += 1

    return_code = data[pos]
    pos += 1

    # Read payload if present
    if pos < len(data):
        # Read string length (varint)
        length = 0
        shift = 0
        while True:
            byte = data[pos]
            pos += 1
            length |= (byte & 0x7f) << shift
            if (byte & 0x80) == 0:
                break
            shift += 7

        payload = data[pos:pos+length]
    else:
        payload = b""

    return ack_flags, return_code, payload
```

#### 5. Realtime Client

```python
import ssl
import socket
import json
from instagrapi import Client

class InstagramRealtimeClient:
    """Instagram Realtime MQTT client."""

    def __init__(self, ig_client: Client):
        self.ig = ig_client
        self.sock = None
        self.callbacks = {}

    def connect(self):
        """Connect to Instagram realtime server."""
        # Get session info
        user_id = int(self.ig.user_id)
        session_id = self.ig.sessionid
        device_id = self.ig.device_id
        user_agent = self.ig.user_agent

        # Build payload
        payload = build_mqttot_connect_payload(
            user_id=user_id,
            session_id=session_id,
            device_id=device_id,
            user_agent=user_agent,
            app_version=self.ig.app_version,
            capabilities_header=self.ig.get_capabilities(),
            language=self.ig.locale,
        )

        # Build CONNECT packet
        connect_packet = build_mqttot_connect_packet(payload, keep_alive=20)

        # Connect to server
        raw_sock = socket.create_connection(("edge-mqtt.facebook.com", 443))
        context = ssl.create_default_context()
        self.sock = context.wrap_socket(raw_sock, server_hostname="edge-mqtt.facebook.com")

        # Send CONNECT
        self.sock.sendall(connect_packet)

        # Receive CONNACK
        connack = self.sock.recv(4096)
        ack, ret_code, payload = parse_mqttot_connack(connack)

        if ret_code != 0:
            raise ConnectionError(f"MQTT connection failed: {ret_code}")

        print(f"Connected! CONNACK payload: {payload}")

        # Subscribe to Iris (DM sync)
        inbox = self.ig.direct_threads()
        self.subscribe_iris(
            seq_id=inbox.get("seq_id"),
            snapshot_at_ms=inbox.get("snapshot_at_ms"),
        )

    def subscribe_iris(self, seq_id: int, snapshot_at_ms: int):
        """Subscribe to Iris message sync."""
        # Build subscription message
        sub_data = {
            "seq_id": seq_id,
            "snapshot_at_ms": snapshot_at_ms,
            "snapshot_app_version": self.ig.app_version,
        }

        # Publish to topic 134 (/ig_sub_iris)
        self.publish(topic_id="134", data=sub_data)

    def publish(self, topic_id: str, data: dict):
        """Publish message to topic."""
        # Compress JSON payload
        payload = json.dumps(data).encode('utf-8')
        compressed = zlib.compress(payload, level=9)

        # Build PUBLISH packet
        packet = self.build_publish_packet(topic_id, compressed)
        self.sock.sendall(packet)

    def receive_messages(self):
        """Receive and parse incoming messages."""
        while True:
            # Receive MQTT packet
            data = self.sock.recv(4096)

            # Parse packet type
            packet_type = (data[0] >> 4) & 0x0f

            if packet_type == 3:  # PUBLISH
                topic_id, payload = self.parse_publish_packet(data)

                # Decompress payload
                if payload[0] == 0x78:  # Zlib magic byte
                    payload = zlib.decompress(payload)

                # Parse based on topic
                if topic_id == "146":  # /ig_message_sync
                    messages = json.loads(payload)
                    for msg in messages:
                        self.handle_dm_message(msg)

    def handle_dm_message(self, msg: dict):
        """Handle incoming DM message."""
        print(f"New message: {msg}")
        # Emit to callbacks, etc.
```

### Key Implementation Notes

1. **Thrift Compact Protocol**: Must implement manually - no library supports the exact variant used by Instagram

2. **Field IDs are critical**: The Thrift schema field numbers MUST match exactly

3. **subscribeTopics field**: Must include topic 146 (`/ig_message_sync`) for DMs

4. **Authentication**: `password` field (field 5) = `f"sessionid={session_id}"`

5. **Compression**: Always use zlib level 9 for outgoing, check 0x78 magic byte for incoming

6. **Iris subscription**: Must call inbox API first to get `seq_id` and `snapshot_at_ms`

7. **Keep-alive**: Use 20 seconds for realtime connection

8. **Message format**: Topic 146 messages are compressed JSON arrays with structure like:
   ```json
   [{
     "event": "patch",
     "data": [{
       "op": "add",
       "path": "/direct_v2/threads/{thread_id}/items/{item_id}",
       "value": {
         "item_id": "...",
         "user_id": 123,
         "timestamp": "...",
         "item_type": "text",
         "text": "message content"
       }
     }]
   }]
   ```

---

## Summary

The Instagram MQTToT protocol is a modified MQTT 3.1.1 with:

1. **Custom protocol name** ("MQTToT" instead of "MQTT")
2. **Thrift Compact Protocol** for CONNECT payload encoding
3. **Zlib compression** (level 9) for all payloads
4. **Session-based authentication** via `password` field in Thrift payload
5. **Topic-based subscriptions** with Iris sync protocol for DMs
6. **Extended CONNACK** with optional payload field

The Thrift schema is well-defined with exact field IDs and types, making it feasible to implement in Python by:
- Manually implementing Thrift Compact Protocol writer
- Using instagrapi for session management
- Building custom MQTT client with MQTToT modifications
- Handling compressed JSON messages on topic 146

All necessary field IDs, types, and encoding details have been documented above for a complete Python port.