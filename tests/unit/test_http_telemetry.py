"""HTTP server spans and durations: route templates only, SSE left out, trace continued."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import httpx2
import pytest
from opentelemetry.trace import SpanKind, StatusCode
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from instagram_mcp import bridge
from instagram_mcp.http_telemetry import HttpServerTelemetry
from instagram_mcp.instruments import HTTP_BUCKETS

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from starlette.requests import Request

    from tests.support.telemetry import Telemetry

THREAD = "340282366841700000000000000000000000001"
PARENT = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"


@pytest.fixture
async def client() -> AsyncIterator[httpx2.AsyncClient]:
    """The bridge app over ASGI (no lifespan: no login, no MQTT) with a mocked gateway."""
    gateway = MagicMock()
    gateway.client.get_thread.return_value = SimpleNamespace(
        thread_id=THREAD, thread_title="t", users=[], is_group=False, messages=[]
    )
    gateway.client.get_pending_threads.return_value = []
    with patch.object(bridge, "gateway", gateway):
        transport = httpx2.ASGITransport(app=bridge.build_app())
        async with httpx2.AsyncClient(transport=transport, base_url="http://bridge") as http:
            yield http


async def test_a_request_span_uses_the_route_never_the_query(
    client: httpx2.AsyncClient, telemetry: Telemetry
) -> None:
    resp = await client.get(
        "/thread", params={"thread_id": THREAD}, headers={"traceparent": PARENT}
    )
    assert resp.status_code == 200
    (span,) = telemetry.named("GET /thread")
    assert span.kind is SpanKind.SERVER
    assert span.attributes is not None
    assert span.attributes["http.route"] == "/thread"
    assert span.attributes["http.response.status_code"] == 200
    assert THREAD not in str(dict(span.attributes))
    assert span.parent is not None
    assert format(span.parent.trace_id, "032x") == "0af7651916cd43dd8448eb211c80319c"


async def test_the_duration_histogram(client: httpx2.AsyncClient, telemetry: Telemetry) -> None:
    labels = {"http.route": "/pending", "http.request.method": "GET"}
    before = telemetry.total("http.server.request.duration", **labels)
    await client.get("/pending")
    assert telemetry.total("http.server.request.duration", **labels) == before + 1
    metric = telemetry.metric("http.server.request.duration")
    assert metric is not None and metric.unit == "s"
    point = next(
        p
        for p in metric.data.data_points
        if dict(p.attributes or {}).get("http.route") == "/pending"
    )
    assert tuple(point.explicit_bounds) == HTTP_BUCKETS
    assert dict(point.attributes or {}) == labels | {"http.response.status_code": 200}


async def test_unknown_paths_have_no_route(
    client: httpx2.AsyncClient, telemetry: Telemetry
) -> None:
    assert (await client.get(f"/threads/{THREAD}")).status_code == 404
    (span,) = telemetry.named("GET")
    assert span.attributes is not None and "http.route" not in span.attributes


def _app() -> Starlette:
    async def boom(_request: Request) -> PlainTextResponse:
        raise RuntimeError(THREAD)

    async def events(_request: Request) -> PlainTextResponse:
        return PlainTextResponse("data: {}\n\n")

    routes = [Route("/boom", boom), Route("/events", events)]
    return Starlette(
        routes=routes, middleware=[Middleware(HttpServerTelemetry, exclude={"/events"})]
    )


async def test_the_sse_stream_is_left_out(telemetry: Telemetry) -> None:
    before = telemetry.total("http.server.request.duration", **{"http.route": "/events"})
    transport = httpx2.ASGITransport(app=_app())
    async with httpx2.AsyncClient(transport=transport, base_url="http://bridge") as http:
        assert (await http.get("/events")).status_code == 200
    assert telemetry.spans() == []
    assert telemetry.total("http.server.request.duration", **{"http.route": "/events"}) == before


async def test_a_crash_is_an_error_without_its_message(telemetry: Telemetry) -> None:
    transport = httpx2.ASGITransport(app=_app(), raise_app_exceptions=False)
    async with httpx2.AsyncClient(transport=transport, base_url="http://bridge") as http:
        assert (await http.get("/boom")).status_code == 500
    (span,) = telemetry.named("GET /boom")
    assert span.status.status_code is StatusCode.ERROR
    assert span.status.description is None
    assert span.attributes is not None
    assert span.attributes["error.type"] == "RuntimeError"
    assert span.events == ()
    labels = {"http.route": "/boom", "error.type": "RuntimeError", "http.response.status_code": 500}
    assert telemetry.total("http.server.request.duration", **labels) >= 1


async def test_odd_methods_and_websockets_pass_through(telemetry: Telemetry) -> None:
    transport = httpx2.ASGITransport(app=_app())
    async with httpx2.AsyncClient(transport=transport, base_url="http://bridge") as http:
        assert (await http.request("BREW", "/boom")).status_code == 405
    (span,) = telemetry.named("_OTHER /boom")
    assert span.attributes is not None and span.attributes["http.request.method"] == "_OTHER"
    passed: list[str] = []

    async def app(scope: dict[str, str], _receive: object, _send: object) -> None:
        passed.append(scope["type"])

    await HttpServerTelemetry(app)({"type": "lifespan"}, None, None)  # type: ignore[arg-type]
    assert passed == ["lifespan"]
