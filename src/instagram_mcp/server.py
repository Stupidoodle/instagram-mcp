"""Instagram MCP server — a thin client of the instagram-bridge daemon.

This process opens NO Instagram connection. It talks to the bridge over HTTP for
commands/reads and consumes the bridge's domain-event SSE stream, pushing those
events into Claude Code as a channel. Many sessions can run at once because they
all share the bridge's single upstream connection (see instagram_mcp.bridge).
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from instagram_mcp.bridge_client import BridgeClient, stream_events
from instagram_mcp.catch_up import CatchUp, ChannelState, state_path
from instagram_mcp.channel import INSTRUCTIONS, Channel, ChannelError, ChannelMCPServer
from instagram_mcp.config import Settings, get_settings, setup_logging
from instagram_mcp.telemetry import configure_telemetry, persona_identity, shutdown_telemetry
from instagram_mcp.tools import (
    register_channel_tools,
    register_media_tools,
    register_message_tools,
    register_messaging_tools,
    register_thread_tools,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from mcp.server.mcpserver import MCPServer

_mcp: MCPServer | None = None
_bridge: BridgeClient | None = None
_channel: Channel | None = None
_stop_events = threading.Event()


def _thread_describer(bridge: BridgeClient) -> Callable[[str], tuple[str, dict[str, str]]]:
    """Look up a thread's title and member names via the bridge (one HTTP call)."""

    def describe(thread_id: str) -> tuple[str, dict[str, str]]:
        t = bridge.thread(thread_id, amount=1)
        names = {u["user_id"]: (u.get("full_name") or u["username"]) for u in t.get("users", [])}
        return t.get("thread_title", ""), names

    return describe


def create_server(settings: Settings | None = None) -> MCPServer:
    """Create the thin-client MCP server and start consuming the bridge event stream."""
    global _mcp, _bridge, _channel  # noqa: PLW0603

    if settings is None:
        settings = get_settings()
    configure_telemetry(persona_identity())
    logger = setup_logging(settings.log_level)

    _bridge = BridgeClient(settings.instagram_bridge_url)
    self_user_id = _bridge.self_user_id()
    if not self_user_id:
        logger.warning(
            "Bridge %s not reachable yet; the event stream and tools will connect when it is.",
            settings.instagram_bridge_url,
        )

    _channel = Channel(
        self_user_id=self_user_id,
        describe_thread=_thread_describer(_bridge),
        idle_minutes=settings.instagram_idle_minutes,
        idle_backoff_after_minutes=settings.instagram_idle_backoff_after_minutes,
        idle_max_minutes=settings.instagram_idle_max_minutes,
        control_thread=settings.instagram_control_thread,
        debug_prefix=settings.instagram_debug_prefix,
        tz=settings.instagram_tz,
    )
    for alias, thread_id in settings.subscriptions():
        try:
            _channel.subscribe(thread_id, alias)
        except ChannelError as e:
            logger.warning("Skipping INSTAGRAM_SUBSCRIBE entry %s: %s", thread_id, e)
    if settings.instagram_control_thread:
        _channel.subscribe(settings.instagram_control_thread, "control")

    _mcp = ChannelMCPServer(
        "instagram-mcp", instructions=INSTRUCTIONS, middleware=[_channel.middleware]
    )

    # Consume the bridge's domain-event SSE stream on a daemon thread. It reconnects
    # on its own, buffers into the channel until the session attaches, and catches up
    # on whatever this persona missed while it was offline.
    _stop_events.clear()
    channel = _channel
    state = settings.instagram_channel_state or state_path(channel.thread_ids())
    catch_up = CatchUp(ChannelState(state), _bridge.messages, channel.thread_ids, channel.handle)
    threading.Thread(
        target=stream_events,
        args=(settings.instagram_bridge_url, channel.handle, _stop_events.is_set),
        kwargs={"catch_up": catch_up},
        name="bridge-events",
        daemon=True,
    ).start()

    register_channel_tools(_mcp, _channel)
    register_messaging_tools(_mcp, _bridge, _channel)
    register_thread_tools(_mcp, _bridge)
    register_message_tools(_mcp, _bridge)
    register_media_tools(_mcp, _bridge)

    logger.info("Instagram MCP thin client ready (bridge=%s)", settings.instagram_bridge_url)
    return _mcp


def get_mcp() -> MCPServer:
    """Return the current MCP server instance, or raise if not created."""
    if _mcp is None:
        msg = "MCP server not initialized. Call create_server() first."
        raise RuntimeError(msg)
    return _mcp


def get_bridge() -> BridgeClient:
    """Return the current bridge client, or raise if not created."""
    if _bridge is None:
        msg = "Bridge client not initialized. Call create_server() first."
        raise RuntimeError(msg)
    return _bridge


def main() -> None:
    """Main entry point for the MCP server (stdio transport)."""
    try:
        create_server().run(transport="stdio")
    except KeyboardInterrupt:
        _stop_events.set()
    finally:
        shutdown_telemetry()


if __name__ == "__main__":
    main()
