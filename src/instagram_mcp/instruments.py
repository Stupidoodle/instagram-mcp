"""Every metric the bridge and the thin client record, created once on the global meter.

The OpenTelemetry API hands out proxy instruments until ``configure_telemetry``
installs a meter provider, so recording costs nothing while telemetry is off. Names
and attributes follow the DM platform's telemetry contract; attribute values come
from closed sets only (``platform``, ``kind``, ``direction``, ``outcome`` ...), never
from message content, names or ids.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from pathlib import PurePath
from typing import TYPE_CHECKING

from opentelemetry import metrics, trace
from opentelemetry.metrics import Observation
from opentelemetry.trace import SpanKind, StatusCode

from instagram_mcp.video import VIDEO_SUFFIXES

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Mapping

    from opentelemetry.metrics import CallbackOptions
    from opentelemetry.trace import Span
    from opentelemetry.util.types import Attributes

PLATFORM = "instagram"

HTTP_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0)
"""Seconds: one HTTP request to the bridge."""

SEND_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0)
"""Seconds: one platform send, or one media download (with its transcription)."""

meter = metrics.get_meter("instagram_mcp")
tracer = trace.get_tracer("instagram_mcp")


@dataclass
class Sources:
    """What the observable gauges read, set by the process that owns the state."""

    bridge_connected: Callable[[], bool] | None = None
    bridge_sse_clients: Callable[[], int] | None = None
    bridge_queues: Callable[[], Mapping[str, int]] | None = None


sources = Sources()


# ── Closed attribute values ─────────────────────────────────────────────────

_SHARES = {"media_share", "clip", "story_share", "reel_share", "felix_share", "profile"}
_KINDS = {
    "text": "text",
    "link": "text",
    "voice_media": "audio",
    "animated_media": "sticker",
    "like": "sticker",
    "location": "location",
}


def message_kind(item_type: str, media_path: str | None = None, *, share: bool = False) -> str:
    """The contract's message kind for an Instagram item type.

    A photo and a video share the item type ``media``: once downloaded the file
    tells them apart, before that (or for our own sends) it counts as an image.
    """
    if share or item_type in _SHARES or item_type.startswith("xma"):
        return "share"
    if item_type in {"media", "raven_media"}:
        is_video = media_path and PurePath(media_path).suffix.lower() in VIDEO_SUFFIXES
        return "video" if is_video else "image"
    return _KINDS.get(item_type, "other")


def direction(user_id: int | str, self_user_id: str) -> str:
    """``out`` for what the account sent (from any device), ``in`` for the other side."""
    return "out" if str(user_id) == self_user_id else "in"


@contextlib.contextmanager
def span(
    name: str,
    *,
    kind: SpanKind = SpanKind.INTERNAL,
    attributes: Attributes = None,
) -> Iterator[Span]:
    """A current span that records a failure as its error class, never the exception text."""
    with tracer.start_as_current_span(
        name,
        kind=kind,
        attributes=attributes,
        record_exception=False,
        set_status_on_exception=False,
    ) as current:
        try:
            yield current
        except Exception as exc:
            current.set_attribute("error.type", type(exc).__qualname__)
            current.set_status(StatusCode.ERROR)
            raise


# ── Bridge: HTTP ────────────────────────────────────────────────────────────

http_server_duration = meter.create_histogram(
    "http.server.request.duration",
    unit="s",
    description="Duration of HTTP server requests (the SSE stream excluded).",
    explicit_bucket_boundaries_advisory=HTTP_BUCKETS,
)
"""Attributes: http.request.method, http.route, http.response.status_code, error.type."""


# ── Bridge: the MQTT connection ─────────────────────────────────────────────

connection_events = meter.create_counter(
    "dm.bridge.connection.events",
    unit="{event}",
    description="MQTT connection events: connected, disconnected, reconnect, "
    "connect_failed, stream_error.",
)


def connection_event(event: str) -> None:
    """Count one MQTT connection event (a closed value, see ``connection_events``)."""
    connection_events.add(1, {"platform": PLATFORM, "event": event})


def _bridge_connected(_options: CallbackOptions) -> Iterable[Observation]:
    probe = sources.bridge_connected
    return [] if probe is None else [Observation(int(probe()), {"platform": PLATFORM})]


meter.create_observable_gauge(
    "dm.bridge.connected",
    callbacks=[_bridge_connected],
    unit="{connection}",
    description="1 while the MQTT connection is up, else 0.",
)


# ── Bridge: events ──────────────────────────────────────────────────────────

bridge_messages = meter.create_counter(
    "dm.bridge.messages",
    unit="{message}",
    description="Messages and reactions that arrived live over MQTT, either direction.",
)
"""Attributes: platform, direction, kind."""

events_published = meter.create_counter(
    "dm.bridge.events.published",
    unit="{event}",
    description="Events the bridge published on its SSE stream.",
)
"""Attributes: platform, type (message reaction read typing unsent thread)."""

events_dropped = meter.create_counter(
    "dm.bridge.events.dropped",
    unit="{event}",
    description="Events a subscriber did not get.",
)
"""Attributes: platform, reason (slow_subscriber)."""

media_duration = meter.create_histogram(
    "dm.bridge.media.duration",
    unit="s",
    description="Fetching inbound media before its event goes out: download, "
    "transcription of a voice note, caption and cover of a share.",
    explicit_bucket_boundaries_advisory=SEND_BUCKETS,
)
"""Attributes: platform, kind, outcome."""


def _sse_clients(_options: CallbackOptions) -> Iterable[Observation]:
    probe = sources.bridge_sse_clients
    return [] if probe is None else [Observation(probe(), {"platform": PLATFORM})]


def _bridge_queues(_options: CallbackOptions) -> Iterable[Observation]:
    probe = sources.bridge_queues
    depths = {} if probe is None else probe()
    return [Observation(n, {"platform": PLATFORM, "queue": q}) for q, n in depths.items()]


meter.create_observable_gauge(
    "dm.bridge.sse.clients",
    callbacks=[_sse_clients],
    unit="{client}",
    description="Subscribers connected to the SSE stream.",
)
meter.create_observable_gauge(
    "dm.bridge.queue.depth",
    callbacks=[_bridge_queues],
    unit="{item}",
    description="Items waiting: media (events held for their download), "
    "sse_backlog (frames queued for subscribers), event_log (frames kept for replay).",
)
