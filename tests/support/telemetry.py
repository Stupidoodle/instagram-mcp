"""In-memory telemetry for tests: the spans, metrics and log records a process recorded.

The OpenTelemetry API takes one global provider per process, so ``tests/conftest.py``
installs a tracer, a meter and a logger provider once, when the suite starts (a simple
span processor, never a batch one whose thread would outlive a test), and the
``telemetry`` fixture clears spans and logs per test. Metrics are cumulative over the
session: tests compare a value before and after, or use labels no other test uses.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader, Metric
    from opentelemetry.sdk.trace import ReadableSpan
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


@dataclass
class Telemetry:
    """What the process recorded."""

    exporter: InMemorySpanExporter
    reader: InMemoryMetricReader
    logs: InMemoryLogRecordExporter

    def clear(self) -> None:
        """Forget the spans and log records recorded so far."""
        self.exporter.clear()
        self.logs.clear()

    def spans(self) -> list[ReadableSpan]:
        """Every finished span."""
        return list(self.exporter.get_finished_spans())

    def named(self, name: str) -> list[ReadableSpan]:
        """The finished spans called ``name``."""
        return [s for s in self.spans() if s.name == name]

    def metric(self, name: str) -> Metric | None:
        """One metric as collected now, or None if nothing recorded it yet."""
        data = self.reader.get_metrics_data()
        for resource in data.resource_metrics if data else []:
            for scope in resource.scope_metrics:
                for metric in scope.metrics:
                    if metric.name == name:
                        return metric
        return None

    def total(self, name: str, **labels: Any) -> float:
        """A metric's value over its points with these labels (histogram: count)."""
        metric = self.metric(name)
        found = 0.0
        for point in metric.data.data_points if metric else []:
            attributes = dict(point.attributes or {})
            if all(attributes.get(k) == v for k, v in labels.items()):
                value = getattr(point, "value", None)
                found += value if value is not None else point.count
        return found

    def points(self, name: str) -> list[dict[str, Any]]:
        """The attributes of every data point of one metric."""
        metric = self.metric(name)
        return [dict(p.attributes or {}) for p in (metric.data.data_points if metric else [])]
