"""HTTP server spans and request durations for the bridge's Starlette app.

A small ASGI middleware instead of opentelemetry-instrumentation-asgi: that one
records ``url.query``, and the bridge's query strings carry thread ids
(``/messages?thread_id=...``). This one records the route template only, reads it
from the route Starlette matched, and leaves the long-lived SSE stream out.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from opentelemetry import trace
from opentelemetry.propagate import extract
from opentelemetry.trace import SpanKind, StatusCode

from instagram_mcp import instruments

if TYPE_CHECKING:
    from collections.abc import Collection

    from starlette.types import ASGIApp, Message, Receive, Scope, Send

_METHODS = frozenset(
    {"GET", "HEAD", "POST", "PUT", "DELETE", "CONNECT", "OPTIONS", "TRACE", "PATCH"}
)

tracer = trace.get_tracer("instagram_mcp.http")


class HttpServerTelemetry:
    """One SERVER span and one ``http.server.request.duration`` sample per request.

    The span continues the caller's W3C ``traceparent`` and is named
    ``{method} {route}`` once the route is known. Paths in ``exclude`` get neither:
    an SSE stream lasts as long as its client.
    """

    def __init__(self, app: ASGIApp, *, exclude: Collection[str] = ()) -> None:
        self.app = app
        self._exclude = frozenset(exclude)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Serve one ASGI connection, timing and tracing it when it is an HTTP request."""
        if scope["type"] != "http" or scope["path"] in self._exclude:
            await self.app(scope, receive, send)
            return
        method = scope["method"] if scope["method"] in _METHODS else "_OTHER"
        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope.get("headers", ())}
        status, error_type = 500, None
        start = time.perf_counter()
        with tracer.start_as_current_span(
            method,
            context=extract(headers),
            kind=SpanKind.SERVER,
            attributes={"http.request.method": method, "url.scheme": scope.get("scheme", "http")},
            record_exception=False,
            set_status_on_exception=False,
        ) as span:

            async def send_status(message: Message) -> None:
                nonlocal status
                if message["type"] == "http.response.start":
                    status = message["status"]
                await send(message)

            try:
                await self.app(scope, receive, send_status)
            except Exception as exc:
                error_type = type(exc).__qualname__
                raise
            finally:
                attributes: dict[str, str | int] = {
                    "http.request.method": method,
                    "http.response.status_code": status,
                }
                route = getattr(scope.get("route"), "path", None)
                if route:
                    attributes["http.route"] = route
                    span.update_name(f"{method} {route}")
                if status >= 500:
                    attributes["error.type"] = error_type or str(status)
                    span.set_status(StatusCode.ERROR)
                span.set_attributes(attributes)
                instruments.http_server_duration.record(time.perf_counter() - start, attributes)
