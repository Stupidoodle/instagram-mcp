"""OpenTelemetry for the bridge and the thin client: off unless an endpoint is set.

Without ``OTEL_EXPORTER_OTLP_ENDPOINT`` (or with ``OTEL_SDK_DISABLED=true``) nothing is
installed: the OpenTelemetry API stays a no-op, no exporter dials out, and every span
and metric the code records costs nothing. With it, each process installs one tracer,
one meter and one logger provider that speak OTLP/HTTP to that endpoint. The SDK reads
the standard variables itself: the per-signal endpoints, ``OTEL_METRIC_EXPORT_INTERVAL``,
``OTEL_RESOURCE_ATTRIBUTES`` and ``OTEL_SERVICE_NAME``. ``OTEL_TRACES_EXPORTER``,
``OTEL_METRICS_EXPORTER`` and ``OTEL_LOGS_EXPORTER`` set to ``none`` leave one signal out.

Telemetry never slows or breaks the app: batch processors with bounded queues, exporter
errors logged by the SDK, and a shutdown flush capped at a few seconds.
"""

from __future__ import annotations

import atexit
import contextlib
import importlib.metadata
import os
import socket
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from opentelemetry import _logs, metrics, trace
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ALWAYS_ON, ParentBased

if TYPE_CHECKING:
    from collections.abc import Mapping

    from opentelemetry.sdk._logs import LogRecordProcessor
    from opentelemetry.sdk.metrics.export import MetricReader
    from opentelemetry.sdk.trace import SpanProcessor

BRIDGE = "instagram-bridge"
MCP = "instagram-mcp"
NAMESPACE = "dm"
SHUTDOWN_SECONDS = 5.0

_REPO = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Identity:
    """Who sends: the service, its instance and, for a persona-side process, the persona."""

    service: str
    instance: str
    persona: str | None = None


@dataclass(frozen=True)
class Providers:
    """The providers of one process; None where the signal is switched off."""

    tracer: TracerProvider | None
    meter: MeterProvider | None
    logger: LoggerProvider | None

    def all(self) -> list[TracerProvider | MeterProvider | LoggerProvider]:
        """Every provider that was built."""
        return [p for p in (self.tracer, self.meter, self.logger) if p is not None]


_installed: Providers | None = None
_lock = threading.Lock()


def enabled(env: Mapping[str, str] | None = None) -> bool:
    """Whether telemetry is on: an OTLP endpoint is set and the SDK is not disabled."""
    env = os.environ if env is None else env
    if env.get("OTEL_SDK_DISABLED", "").strip().lower() == "true":
        return False
    return bool(env.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip())


def signal_on(signal: str, env: Mapping[str, str] | None = None) -> bool:
    """Whether one signal (``traces``, ``metrics``, ``logs``) exports; ``none`` turns it off."""
    env = os.environ if env is None else env
    return env.get(f"OTEL_{signal.upper()}_EXPORTER", "otlp").strip().lower() != "none"


def persona_dir(env: Mapping[str, str] | None = None) -> Path:
    """The persona folder: ``DM_PERSONA_DIR``, else ``PWD`` (Claude Code's launch folder)."""
    env = os.environ if env is None else env
    return Path(env.get("DM_PERSONA_DIR") or env.get("PWD") or Path.cwd())


def persona_name(folder: str) -> str:
    """The persona of a ``<name>-<platform>`` folder: ``christine-instagram`` is ``christine``."""
    return folder.rsplit("-", 1)[0]


def bridge_identity() -> Identity:
    """The bridge daemon: one per host."""
    return Identity(BRIDGE, socket.gethostname())


def persona_identity(env: Mapping[str, str] | None = None) -> Identity:
    """A thin client: one per persona folder, so two personas never share a series."""
    folder = persona_dir(env).name
    return Identity(MCP, folder, persona_name(folder))


def service_version() -> str:
    """The deployed git revision when the checkout has one, else the package version."""
    head = _REPO / ".git" / "HEAD"
    with contextlib.suppress(OSError):
        ref = head.read_text().strip()
        if not ref.startswith("ref: "):
            return ref[:12]
        return (_REPO / ".git" / ref.removeprefix("ref: ")).read_text().strip()[:12]
    return importlib.metadata.version("instagram-mcp")


def resource(identity: Identity, env: Mapping[str, str] | None = None) -> Resource:
    """The resource every span, metric and log record carries.

    ``OTEL_SERVICE_NAME`` wins over the default name and ``OTEL_RESOURCE_ATTRIBUTES``
    adds to it (nexi sets ``deployment.environment.name=prod`` there).
    """
    env = os.environ if env is None else env
    attributes: dict[str, str] = {
        "service.namespace": NAMESPACE,
        "service.instance.id": identity.instance,
        "service.version": service_version(),
    }
    if not env.get("OTEL_SERVICE_NAME", "").strip():
        attributes["service.name"] = identity.service
    if identity.persona:
        attributes["dm.persona"] = identity.persona
    return Resource.create(attributes)


def build_providers(
    identity: Identity,
    *,
    env: Mapping[str, str] | None = None,
    span_processor: SpanProcessor | None = None,
    metric_reader: MetricReader | None = None,
    log_processor: LogRecordProcessor | None = None,
) -> Providers:
    """The providers for this process, exporting over OTLP/HTTP.

    Args:
        identity: The service and instance (and persona) sending.
        env: The environment to read the switches from (default ``os.environ``).
        span_processor: Tests only: used in place of the batch OTLP span export.
        metric_reader: Tests only: used in place of the periodic OTLP metric push.
        log_processor: Tests only: used in place of the batch OTLP log export.

    Returns:
        The providers, not yet installed. A signal whose exporter is ``none`` gets
        no provider unless a test processor is given for it.
    """
    env = os.environ if env is None else env
    res = resource(identity, env)
    tracer = meter = logger = None
    if span_processor is not None or signal_on("traces", env):
        tracer = TracerProvider(
            resource=res, sampler=ParentBased(ALWAYS_ON), shutdown_on_exit=False
        )
        tracer.add_span_processor(span_processor or BatchSpanProcessor(OTLPSpanExporter()))
    if metric_reader is not None or signal_on("metrics", env):
        reader = metric_reader or PeriodicExportingMetricReader(OTLPMetricExporter())
        meter = MeterProvider(resource=res, metric_readers=[reader], shutdown_on_exit=False)
    if log_processor is not None or signal_on("logs", env):
        logger = LoggerProvider(resource=res, shutdown_on_exit=False)
        logger.add_log_record_processor(log_processor or BatchLogRecordProcessor(OTLPLogExporter()))
    return Providers(tracer, meter, logger)


def configure_telemetry(identity: Identity, env: Mapping[str, str] | None = None) -> bool:
    """Install the providers when an endpoint is set; a second call is a no-op.

    Args:
        identity: The service and instance (and persona) sending.
        env: The environment to read the switches from (default ``os.environ``).

    Returns:
        True when telemetry is on in this process.
    """
    global _installed  # noqa: PLW0603
    if not enabled(env):
        return False
    with _lock:
        if _installed is not None:
            return True
        providers = build_providers(identity, env=env)
        if providers.tracer is not None:
            trace.set_tracer_provider(providers.tracer)
        if providers.meter is not None:
            metrics.set_meter_provider(providers.meter)
        if providers.logger is not None:
            _logs.set_logger_provider(providers.logger)
        _installed = providers
    atexit.register(shutdown_telemetry)
    return True


def installed() -> Providers | None:
    """The providers ``configure_telemetry`` installed, if any."""
    return _installed


def shutdown_telemetry(timeout: float = SHUTDOWN_SECONDS) -> None:
    """Flush and stop the installed providers, waiting at most ``timeout`` seconds.

    The SDK's own shutdown can wait 30 s on a dead collector; this runs it on a
    daemon thread and gives up after ``timeout``, so exit is never held up for long.
    """
    global _installed
    with _lock:
        providers, _installed = _installed, None
    if providers is None:
        return
    shutdown_providers(providers, timeout)


def shutdown_providers(providers: Providers, timeout: float = SHUTDOWN_SECONDS) -> None:
    """Shut ``providers`` down on a daemon thread, waiting at most ``timeout`` seconds."""

    def run() -> None:
        for provider in providers.all():
            with contextlib.suppress(Exception):
                provider.shutdown()

    worker = threading.Thread(target=run, name="otel-shutdown", daemon=True)
    worker.start()
    worker.join(timeout)
