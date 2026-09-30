"""Telemetry switches, resource and providers: off by default, OTLP when an endpoint is set."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any, ClassVar

import pytest
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from instagram_mcp import telemetry
from instagram_mcp.telemetry import (
    Identity,
    build_providers,
    enabled,
    persona_identity,
    persona_name,
    resource,
    shutdown_providers,
    signal_on,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

ENDPOINT = {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:4318"}


class TestSwitches:
    def test_off_without_an_endpoint(self) -> None:
        assert enabled({}) is False
        assert enabled({"OTEL_EXPORTER_OTLP_ENDPOINT": "  "}) is False

    def test_on_with_an_endpoint(self) -> None:
        assert enabled(ENDPOINT) is True

    def test_sdk_disabled_wins(self) -> None:
        assert enabled(ENDPOINT | {"OTEL_SDK_DISABLED": "TRUE"}) is False

    def test_each_signal_can_be_left_out(self) -> None:
        env = {"OTEL_LOGS_EXPORTER": "none", "OTEL_TRACES_EXPORTER": "otlp"}
        assert signal_on("logs", env) is False
        assert signal_on("traces", env) is True
        assert signal_on("metrics", env) is True

    def test_configure_installs_once_and_shuts_down(
        self, collector: tuple[str, type[_Collector]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        installed: list[object] = []
        for module, setter in (
            (telemetry.trace, "set_tracer_provider"),
            (telemetry.metrics, "set_meter_provider"),
            (telemetry._logs, "set_logger_provider"),
        ):
            monkeypatch.setattr(module, setter, installed.append)
        env = {"OTEL_EXPORTER_OTLP_ENDPOINT": collector[0]}
        assert telemetry.configure_telemetry(Identity("x", "y"), env=env) is True
        providers = telemetry.installed()
        assert telemetry.configure_telemetry(Identity("x", "y"), env=env) is True
        assert telemetry.installed() is providers
        assert providers is not None
        assert installed == providers.all()
        assert len(installed) == 3
        telemetry.shutdown_telemetry()
        assert telemetry.installed() is None

    def test_configure_is_a_no_op_when_off(self) -> None:
        assert telemetry.configure_telemetry(Identity("x", "y"), env={}) is False
        assert telemetry.installed() is None
        telemetry.shutdown_telemetry()  # nothing installed: nothing to do


class TestIdentity:
    def test_persona_from_the_folder_name(self) -> None:
        assert persona_name("christine-instagram") == "christine"
        assert persona_name("ly-whatsapp") == "ly"
        assert persona_name("solo") == "solo"

    def test_persona_folder_from_pwd_or_override(self, tmp_path: Path) -> None:
        pwd = {"PWD": str(tmp_path / "mini-instagram")}
        assert persona_identity(pwd) == Identity("instagram-mcp", "mini-instagram", "mini")
        override = pwd | {"DM_PERSONA_DIR": "/x/christine-instagram"}
        assert persona_identity(override).persona == "christine"

    def test_the_bridge_is_one_per_host(self) -> None:
        identity = telemetry.bridge_identity()
        assert identity.service == "instagram-bridge"
        assert identity.persona is None
        assert identity.instance


class TestResource:
    def test_attributes(self) -> None:
        attributes = resource(Identity("instagram-mcp", "mini-instagram", "mini"), {}).attributes
        assert attributes["service.name"] == "instagram-mcp"
        assert attributes["service.namespace"] == "dm"
        assert attributes["service.instance.id"] == "mini-instagram"
        assert attributes["dm.persona"] == "mini"
        assert attributes["service.version"]

    def test_the_bridge_has_no_persona(self) -> None:
        assert "dm.persona" not in resource(Identity("instagram-bridge", "nexi"), {}).attributes

    def test_env_service_name_and_attributes_are_honoured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OTEL_SERVICE_NAME", "renamed")
        monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", "deployment.environment.name=prod")
        env = {"OTEL_SERVICE_NAME": "renamed"}
        attributes = resource(Identity("instagram-bridge", "nexi"), env).attributes
        assert attributes["service.name"] == "renamed"
        assert attributes["deployment.environment.name"] == "prod"
        assert attributes["service.instance.id"] == "nexi"


class TestBuildProviders:
    def test_test_processors_carry_the_resource(self) -> None:
        spans, reader = InMemorySpanExporter(), InMemoryMetricReader()
        providers = build_providers(
            Identity("instagram-bridge", "nexi"),
            env={"OTEL_LOGS_EXPORTER": "none"},
            span_processor=SimpleSpanProcessor(spans),
            metric_reader=reader,
        )
        assert providers.tracer is not None and providers.meter is not None
        assert providers.logger is None
        providers.tracer.get_tracer("t").start_span("s").end()
        providers.meter.get_meter("t").create_counter("c").add(1)
        (span,) = spans.get_finished_spans()
        assert span.resource.attributes["service.name"] == "instagram-bridge"
        data = reader.get_metrics_data()
        assert data is not None
        assert data.resource_metrics[0].resource.attributes["service.instance.id"] == "nexi"
        shutdown_providers(providers)

    def test_none_leaves_a_signal_out(self) -> None:
        env = {f"OTEL_{s}_EXPORTER": "none" for s in ("TRACES", "METRICS", "LOGS")}
        assert build_providers(Identity("x", "y"), env=env).all() == []

    def test_parent_based_always_on(self) -> None:
        spans = InMemorySpanExporter()
        providers = build_providers(
            Identity("x", "y"),
            env={"OTEL_METRICS_EXPORTER": "none", "OTEL_LOGS_EXPORTER": "none"},
            span_processor=SimpleSpanProcessor(spans),
        )
        assert providers.tracer is not None
        assert "ParentBased" in providers.tracer.sampler.get_description()
        shutdown_providers(providers)


class _Collector(BaseHTTPRequestHandler):
    """A loopback OTLP collector: records what was posted, optionally hangs."""

    posts: ClassVar[list[tuple[str, str]]] = []
    hang = 0.0

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("content-length", 0)))
        self.posts.append((self.path, self.headers.get("content-type", "")))
        time.sleep(self.hang)
        self.send_response(200)
        self.send_header("content-length", "0")
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        """Keep the test output quiet."""


@pytest.fixture
def collector() -> Iterator[tuple[str, type[_Collector]]]:
    handler = type("Collector", (_Collector,), {"posts": [], "hang": 0.0})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", handler
    finally:
        server.shutdown()
        server.server_close()


_SCRIPT = """
import json, logging, sys, time
from opentelemetry import _logs, metrics, trace
from instagram_mcp import telemetry
on = telemetry.configure_telemetry(telemetry.bridge_identity())
again = telemetry.configure_telemetry(telemetry.bridge_identity())
tracer_provider = type(trace.get_tracer_provider()).__name__
trace.get_tracer("t").start_span("s").end()
metrics.get_meter("t").create_counter("c").add(1)
_logs.get_logger("t").emit(body="hello")
start = time.monotonic()
telemetry.shutdown_telemetry(timeout=float(sys.argv[1]))
print(json.dumps({"on": on, "again": again, "tracer_provider": tracer_provider,
                  "shutdown": time.monotonic() - start}))
"""


def _run(env: dict[str, str], timeout: float = 5.0) -> dict[str, Any]:
    clean = {k: v for k, v in os.environ.items() if not k.startswith("OTEL_")}
    result = subprocess.run(  # noqa: S603 - our own interpreter and script, fixed argv
        [sys.executable, "-c", _SCRIPT, str(timeout)],
        env=clean | env,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    out: dict[str, Any] = json.loads(result.stdout.strip().splitlines()[-1])
    return out


class TestProcess:
    def test_off_by_default_installs_nothing(self) -> None:
        out = _run({})
        assert out == out | {"on": False, "again": False, "tracer_provider": "ProxyTracerProvider"}

    def test_on_exports_every_signal_over_otlp_http(
        self, collector: tuple[str, type[_Collector]]
    ) -> None:
        url, handler = collector
        out = _run({"OTEL_EXPORTER_OTLP_ENDPOINT": url})
        assert (out["on"], out["again"], out["tracer_provider"]) == (True, True, "TracerProvider")
        assert {path for path, _ in handler.posts} == {"/v1/traces", "/v1/metrics", "/v1/logs"}
        assert {kind for _, kind in handler.posts} == {"application/x-protobuf"}

    def test_a_dead_collector_does_not_hold_the_exit(
        self, collector: tuple[str, type[_Collector]]
    ) -> None:
        url, handler = collector
        handler.hang = 30.0
        out = _run({"OTEL_EXPORTER_OTLP_ENDPOINT": url}, timeout=1.0)
        assert out["on"] is True
        assert out["shutdown"] < 3.0
