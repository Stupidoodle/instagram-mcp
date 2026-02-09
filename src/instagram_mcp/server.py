"""Instagram MCP Server entry point.

This module provides the main MCP server that exposes Instagram Direct Message
functionality through the Model Context Protocol.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send

from instagram_mcp.client import AuthenticationError, InstagramClient, SessionError
from instagram_mcp.config import Settings, get_settings, setup_logging
from instagram_mcp.mqtt.manager import MQTTManager
from instagram_mcp.tools import (
    register_media_tools,
    register_message_tools,
    register_thread_tools,
)

# Global instances
_mcp: FastMCP | None = None
_client: InstagramClient | None = None
_mqtt_manager: MQTTManager | None = None
_http_transport: bool = False

# HTTP transport timeout: Claude Code enforces 60s per-request; cap tools at 50s.
HTTP_TOOL_TIMEOUT_SECONDS: int = 50


def create_server(
    settings: Settings | None = None,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> FastMCP:
    """Create and configure the MCP server.

    Args:
        settings: Optional settings instance. If not provided, loads from environment.
        host: Bind address for HTTP transport (ignored for stdio).
        port: Bind port for HTTP transport (ignored for stdio).

    Returns:
        FastMCP: Configured MCP server instance.

    Raises:
        AuthenticationError: If Instagram authentication fails.
        SessionError: If session loading/saving fails.
    """
    global _mcp, _client  # noqa: PLW0603

    if settings is None:
        settings = get_settings()

    logger = setup_logging(settings.log_level)

    # Initialize MCP server
    _mcp = FastMCP("instagram-mcp", host=host, port=port, stateless_http=True)

    # Initialize Instagram client
    _client = InstagramClient(
        session_file=settings.instagram_session_file,
        app_version=settings.instagram_app_version,
    )

    # Try to load session or login
    try:
        _client.login_or_load_session(
            username=settings.instagram_username,
            password=settings.instagram_password.get_secret_value(),
        )
    except (AuthenticationError, SessionError) as e:
        logger.error("Authentication failed: %s", e)
        logger.error("Run 'instagram-mcp-login' to authenticate interactively first.")
        raise

    # Initialize MQTT realtime (non-fatal — falls back to REST polling)
    global _mqtt_manager  # noqa: PLW0603
    try:
        iris = _client.get_iris_info()
        _mqtt_manager = MQTTManager()
        _mqtt_manager.connect(
            session_file=settings.instagram_session_file,
            seq_id=iris["seq_id"],
            snapshot_at_ms=iris["snapshot_at_ms"],
            app_version=iris["app_version"],
        )
        logger.info(
            "MQTT realtime connected (seq_id=%d, snapshot=%d)",
            iris["seq_id"],
            iris["snapshot_at_ms"],
        )
    except Exception:
        logger.warning("MQTT connection failed, falling back to REST polling", exc_info=True)
        _mqtt_manager = None

    # Register all tools
    register_thread_tools(_mcp, _client)
    register_message_tools(_mcp, _client)
    register_media_tools(_mcp, _client)

    logger.info("Instagram MCP server initialized successfully")
    return _mcp


def get_mcp() -> FastMCP:
    """Get the current MCP server instance.

    Returns:
        FastMCP: The MCP server instance.

    Raises:
        RuntimeError: If server hasn't been created yet.
    """
    if _mcp is None:
        raise RuntimeError("MCP server not initialized. Call create_server() first.")
    return _mcp


def is_http_transport() -> bool:
    """Return True when running over HTTP (shared daemon mode).

    Long-running tools check this to cap execution time within the
    client's per-request timeout (``HTTP_TOOL_TIMEOUT_SECONDS``).
    """
    return _http_transport


def get_mqtt_manager() -> MQTTManager | None:
    """Get the current MQTT manager instance, if connected.

    Returns:
        MQTTManager if MQTT is active, None otherwise.
    """
    return _mqtt_manager


def get_client() -> InstagramClient:
    """Get the current Instagram client instance.

    Returns:
        InstagramClient: The Instagram client instance.

    Raises:
        RuntimeError: If client hasn't been created yet.
    """
    if _client is None:
        raise RuntimeError("Instagram client not initialized. Call create_server() first.")
    return _client


def main() -> None:
    """Main entry point for the MCP server.

    Supports both stdio (default, for Claude Code ``mcp add``) and
    streamable-http (shared daemon for multiple clients).

    Usage::

        instagram-mcp                              # stdio (default)
        instagram-mcp --transport streamable-http   # HTTP daemon on :8000
        instagram-mcp --transport streamable-http --port 9000 --host 0.0.0.0
    """
    import argparse

    parser = argparse.ArgumentParser(description="Instagram MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default="stdio",
        help="Transport protocol (default: stdio)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address for HTTP transport (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Bind port for HTTP transport (default: 8000)",
    )
    args = parser.parse_args()

    try:
        mcp = create_server(host=args.host, port=args.port)
        if args.transport == "streamable-http":
            _run_http_server(mcp, args.host, args.port)
        else:
            mcp.run(transport="stdio")
    except (AuthenticationError, SessionError) as e:
        print(f"Failed to start server: {e}", file=sys.stderr)
        print("Run 'instagram-mcp-login' to authenticate first.", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(0)


def _run_http_server(mcp: FastMCP, host: str, port: int) -> None:
    """Run the MCP server over streamable-http with GET short-circuit middleware."""
    global _http_transport  # noqa: PLW0603
    _http_transport = True

    import anyio

    async def _run() -> None:
        import uvicorn

        app = mcp.streamable_http_app()
        wrapped = _GetShortCircuitMiddleware(app)
        config = uvicorn.Config(wrapped, host=host, port=port, log_level="info")
        server = uvicorn.Server(config)
        await server.serve()

    anyio.run(_run)


class _GetShortCircuitMiddleware:
    """ASGI middleware that returns a lightweight SSE response for GET /mcp.

    Claude Code's MCP client:
    1. Sends GET /mcp as a connectivity test (must complete within 30 s).
    2. Opens GET /mcp for an SSE notification stream (and reconnects on drop).

    FastMCP's streamable-http handler always creates a long-lived SSE stream
    that never completes, causing the connectivity test to hang and
    reconnection attempts to break.

    This middleware returns a valid SSE response that completes immediately.
    The ``text/event-stream`` content-type lets the client treat it as a
    successful SSE connection, and the immediate stream-end keeps both the
    connectivity test and reconnection fast.
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] == "GET":
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [
                        [b"content-type", b"text/event-stream"],
                        [b"cache-control", b"no-cache, no-transform"],
                    ],
                }
            )
            # SSE retry directive: wait 5 min before reconnecting.
            # Prevents the client from hammering GET in a tight loop.
            await send({"type": "http.response.body", "body": b"retry: 300000\n: ok\n\n"})
            return
        await self._app(scope, receive, send)


def serve() -> None:
    """Start the MCP server in HTTP daemon mode.

    Convenience entry point that defaults to streamable-http transport.
    Equivalent to ``instagram-mcp --transport streamable-http``.
    """
    import argparse

    parser = argparse.ArgumentParser(description="Instagram MCP Server (HTTP daemon)")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000)")
    args = parser.parse_args()

    try:
        mcp = create_server(host=args.host, port=args.port)
        _run_http_server(mcp, args.host, args.port)
    except (AuthenticationError, SessionError) as e:
        print(f"Failed to start server: {e}", file=sys.stderr)
        print("Run 'instagram-mcp-login' to authenticate first.", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
