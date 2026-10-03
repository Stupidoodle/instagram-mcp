# Observability

How the Instagram bridge and the thin-client MCP report on themselves: what each process
sends, under which names, and what never leaves. Both follow the DM platform's telemetry
contract, which the WhatsApp bridge and channel share, so the dashboards query both
platforms with the same names and a `platform` label. A rename is a breaking change.

| process | `service.name` | `service.instance.id` |
|---|---|---|
| the bridge (one Instagram login, MQTT, SSE fan-out, HTTP API) | `instagram-bridge` | the hostname |
| the thin client (MCP server and Claude Code channel, one per persona session) | `instagram-mcp` | the persona folder (`dm.persona` is its name) |

Switches: see the README's Telemetry section. Off unless `OTEL_EXPORTER_OTLP_ENDPOINT` is
set; OTLP/HTTP protobuf; `OTEL_<SIGNAL>_EXPORTER=none` drops a signal. Every resource has
`service.namespace=dm`.

## Privacy

Metadata only. Never in a span (name, attribute, event, link), a metric attribute, or a log
line at info or above, hashed or not: message text, captions, transcripts, names,
usernames, user ids, thread ids, query strings, tool arguments and results, and exception
messages (only the class is kept). Allowed: platform message ids (`item_id`, random), the
persona name, the alias a persona subscribed a chat with (thin client only), direction,
kind, sizes, durations, counts, route templates, status codes, error classes.
`tests/unit/test_bridge_telemetry.py::TestNoTextLeaves` searches every signal for a thread
id, a user id, a text and a transcript after a realistic flow.

## Metrics

Prometheus 3 names: dots become underscores, unit `s` adds `_seconds`, counters get
`_total`. Attribute values are closed sets. `platform` is `instagram`; `kind` is one of
`text image video audio document sticker reaction poll share location contact other`;
`direction` is `in` or `out` (the account, from any device); `outcome` is `ok` or `error`.

### Bridge

| instrument | type, unit | attributes |
|---|---|---|
| `dm.bridge.messages` | counter `{message}` | platform, direction, kind |
| `dm.bridge.send.duration` | histogram `s` | platform, kind, outcome |
| `dm.bridge.media.duration` | histogram `s` | platform, kind, outcome |
| `dm.bridge.reply.duration` | histogram `s` | platform, side |
| `dm.bridge.connection.events` | counter `{event}` | platform, event |
| `dm.bridge.connected` | gauge `{connection}`, 1 or 0 | platform |
| `dm.bridge.last_event.timestamp` | gauge `s` (Unix time) | platform, type |
| `dm.bridge.sse.clients` | gauge `{client}` | platform |
| `dm.bridge.events.published` | counter `{event}` | platform, type |
| `dm.bridge.events.dropped` | counter `{event}` | platform, reason |
| `dm.bridge.queue.depth` | gauge `{item}` | platform, queue |
| `http.server.request.duration` | histogram `s` (semconv) | http.request.method, http.route, http.response.status_code, error.type |

- `event` (MQTT): `connected disconnected reconnect connect_failed stream_error`.
- `type`: `message reaction read typing unsent thread`, plus `other` on the freshness gauge.
  `reason`: `slow_subscriber`. `queue`: `media` (events held for their download),
  `sse_backlog`, `event_log` (frames kept for replay).
- `dm.bridge.last_event.timestamp` is stamped by every MQTT event the bridge gets. No point
  until the first event of a type, so alert on `absent()` as well as on age.
- `dm.bridge.reply.duration` times replies live with the persona retro's rule: a message
  after the other side's last one, less than 2 h later; reactions and edits are no
  messages. Only the other side's messages and sends through the bridge's API feed it, so
  `side="me"` is a persona's reply and `side="them"` an answer to one, and chats nothing
  was sent to through the API never count. The account's own messages that come back over
  MQTT are left out on purpose: Instagram echoes the bridge's own sends, sometimes before
  the send call returns. The price is that a message the owner sends from the phone into a
  persona's thread is not seen. Buckets (seconds) `5 15 30 60 120 300 600 900 1800 3600
  7200`, the same as the WhatsApp bridge.
- Other buckets (seconds): send and media `0.05 0.1 0.25 0.5 1 2 5 10 30 60 120`; HTTP
  `0.005 0.01 0.025 0.05 0.1 0.25 0.5 1 2.5 5 10 30`. `http.route` is the route template;
  the SSE stream has no duration sample.

### Thin client

| instrument | type, unit | attributes |
|---|---|---|
| `dm.channel.messages` | counter `{message}` | persona, platform, direction, kind |
| `dm.channel.notifications` | counter `{notification}` | persona, platform, type, outcome |
| `dm.channel.stream.reconnects` | counter `{reconnect}` | persona, platform, reason (`ended error http_status`) |
| `dm.channel.stream.connected` | gauge `{connection}` | persona, platform |
| `dm.channel.catchup.events` | counter `{event}` | persona, platform, mode (`replay backfill`) |
| `dm.channel.queue.depth` | gauge `{item}` | persona, platform, queue (`pending`) |
| `mcp.server.operation.duration` | histogram `s` (semconv) | mcp.method.name, gen_ai.tool.name, persona, error.type |

`mcp.server.operation.duration` times every MCP method here; filter
`mcp_method_name="tools/call"` for tool calls.

### Exemplars

The Python SDK attaches the current trace and span id to samples recorded inside a
sampled span, and Prometheus stores them. Turn exemplars on for a histogram query in
Grafana and click a dot to open the trace.

## Traces

| span | kind | where | attributes |
|---|---|---|---|
| `instagram.event <type>` | CONSUMER, root | bridge, per MQTT event | `dm.platform`, `dm.event.type`, `dm.direction`, `messaging.message.id`, `dm.message.kind`, on a counted reply `dm.reply.side`, `dm.reply.seconds` |
| `instagram.media.download`, `instagram.share.describe` | INTERNAL | bridge, under the event span | `dm.platform`, `messaging.message.id` |
| `POST /transcribe` | CLIENT | bridge, a voice note's transcription | `http.request.method`, `server.address`, `server.port`, `url.full` (a fixed loopback URL), `http.response.status_code` |
| `instagram.send` | CLIENT | bridge, every send | `dm.platform`, `dm.message.kind`, `dm.message.bytes`, `dm.outcome`, `error.type`, on a counted reply `dm.reply.side`, `dm.reply.seconds` |
| `<METHOD> <route>` | SERVER | bridge, every HTTP request but the SSE stream | `http.request.method`, `http.route`, `http.response.status_code` |
| `dm.channel.deliver` | CONSUMER | thin client, per event it handles | `dm.platform`, `dm.persona`, `dm.event.type`, `dm.delivery` (`live replay`), `messaging.message.id` |
| `tools/call <tool>` | SERVER | thin client (the MCP SDK's span) | `mcp.method.name`, `gen_ai.tool.name`, `gen_ai.operation.name`, `dm.persona`, `error.type` |

How a message becomes traces:

1. Inbound: the MQTT event opens `instagram.event message`; its download and transcription
   are children. The event's SSE frame carries the span's `traceparent`; the thin client
   continues it with `dm.channel.deliver` and drops the field before anything reaches
   Claude Code.
2. Claude Code carries no trace context into the tool call that answers, so a reply starts
   a new trace: `tools/call reply`, then the bridge's `POST /send` and `instagram.send`.
3. The bridge ties them together: a send that counts as a reply gets a span link
   (`dm.link.type=reply_to`) to the event span of the message it answers, and the other
   side's answer links back to the send. The reply latency is recorded inside that span,
   so its exemplar opens the reply's trace and the link leads to the inbound one.

## Logs

One JSON object per line: `time`, `level`, `msg` (a constant sentence), `service`,
`trace_id`/`span_id` inside a span, then metadata (`message_id`, `kind`, `error_type`...).
The bridge writes to stdout (the journal on a systemd host, with OTLP logs off); the thin
client writes to stderr and, when on, over OTLP. Other libraries log at warning and above
only, since instagrapi logs request URLs with ids at info. Levels: `error` needs a person,
`warn` is a failure the code recovers from (a dropped MQTT socket the watchdog reconnects),
`info` is the normal flow.

## Health

| route | 200 when | otherwise |
|---|---|---|
| `GET /health` | the process serves HTTP (liveness). Also returns `self_user_id` and `mqtt_connected`; the thin client reads the id from it. | no answer |
| `GET /ready` | the account is logged in and MQTT is connected (readiness). No ids. | 503, `{"ready": false, "reason": "not_logged_in"}` or `"disconnected"` (the watchdog reconnects) |

The bridge listens on `127.0.0.1:8082` by default.

## Runbook hints

| question | where to look |
|---|---|
| Usable right now? | `curl -s 127.0.0.1:8082/ready` on the host. |
| MQTT flapping? | `increase(dm_bridge_connection_events_total{platform="instagram"}[1h])` by `event`; `dm_bridge_connected`. |
| Still hearing from Instagram? | `time() - max by (type) (dm_bridge_last_event_timestamp_seconds{platform="instagram"})` while `dm_bridge_connected` is 1. |
| Is a persona receiving? | `dm_channel_stream_connected` by `instance`, `dm_channel_notifications_total{outcome="error"}`, `dm_bridge_sse_clients`. |
| Sends slow or failing? | `histogram_quantile(0.9, sum by (le) (rate(dm_bridge_send_duration_seconds_bucket{platform="instagram"}[5m])))`; `outcome="error"`; click an exemplar. |
| How fast does a persona answer? | `histogram_quantile(0.5, sum by (le, side) (rate(dm_bridge_reply_duration_seconds_bucket{platform="instagram"}[1d])))`; Tempo `{span.dm.reply.side="me" && span.dm.reply.seconds > 600}` for the slow ones. |
| One message end to end | Tempo: `{resource.service.namespace="dm" && span.messaging.message.id="<item_id>"}`. |
