"""Every metric the bridge and the thin client record, created once on the global meter.

The OpenTelemetry API hands out proxy instruments until ``configure_telemetry``
installs a meter provider, so recording costs nothing while telemetry is off. Names
and attributes follow the DM platform's telemetry contract; attribute values come
from closed sets only (``platform``, ``kind``, ``direction``, ``outcome`` ...), never
from message content, names or ids.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from opentelemetry import metrics
from opentelemetry.metrics import Observation

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from opentelemetry.metrics import CallbackOptions

PLATFORM = "instagram"

HTTP_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0)
"""Seconds: one HTTP request to the bridge."""

meter = metrics.get_meter("instagram_mcp")

http_server_duration = meter.create_histogram(
    "http.server.request.duration",
    unit="s",
    description="Duration of HTTP server requests (the SSE stream excluded).",
    explicit_bucket_boundaries_advisory=HTTP_BUCKETS,
)
"""Attributes: http.request.method, http.route, http.response.status_code, error.type."""


@dataclass
class Sources:
    """What the observable gauges read, set by the process that owns the state."""

    bridge_connected: Callable[[], bool] | None = None


sources = Sources()


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
