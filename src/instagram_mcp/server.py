"""Instagram MCP Server entry point.

This module provides the main MCP server that exposes Instagram Direct Message
functionality through the Model Context Protocol.
"""

from __future__ import annotations

import sys

from mcp.server.fastmcp import FastMCP

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


def create_server(settings: Settings | None = None) -> FastMCP:
    """Create and configure the MCP server.

    Args:
        settings: Optional settings instance. If not provided, loads from environment.

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
    _mcp = FastMCP("instagram-mcp")

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
    """Main entry point for the MCP server (stdio transport)."""
    try:
        mcp = create_server()
        mcp.run(transport="stdio")
    except (AuthenticationError, SessionError) as e:
        print(f"Failed to start server: {e}", file=sys.stderr)
        print("Run 'instagram-mcp-login' to authenticate first.", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
