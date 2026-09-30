"""Every metric the bridge and the thin client record, created once on the global meter.

The OpenTelemetry API hands out proxy instruments until ``configure_telemetry``
installs a meter provider, so recording costs nothing while telemetry is off. Names
and attributes follow the DM platform's telemetry contract; attribute values come
from closed sets only (``platform``, ``kind``, ``direction``, ``outcome`` ...), never
from message content, names or ids.
"""

from __future__ import annotations

from opentelemetry import metrics

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
