"""Unit tests for the thin-client MCP server.

``create_server`` opens no Instagram connection: it constructs a ``BridgeClient``,
asks the bridge for the logged-in user id, starts a daemon thread that consumes
the bridge's SSE stream, and registers the tools. These tests patch those
collaborators so the server builds fully offline.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from instagram_mcp import server
from instagram_mcp.server import create_server, get_bridge, get_mcp, main

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def patched_server() -> Iterator[SimpleNamespace]:
    """Patch create_server's collaborators so it builds without any network."""
    bridge = MagicMock()
    bridge.self_user_id.return_value = "42"
    bridge.thread.return_value = {"thread_title": "", "users": []}
    with (
        patch("instagram_mcp.server.setup_logging"),
        patch("instagram_mcp.server.BridgeClient", return_value=bridge) as bridge_cls,
        patch("instagram_mcp.server.stream_events") as stream,
        patch("instagram_mcp.server.threading.Thread") as thread_cls,
    ):
        yield SimpleNamespace(
            bridge=bridge, bridge_cls=bridge_cls, stream=stream, thread_cls=thread_cls
        )


class TestCreateServer:
    def test_builds_thin_client(self, patched_server: SimpleNamespace, mock_settings) -> None:
        mcp = create_server(mock_settings)

        assert mcp is not None
        assert mcp.name == "instagram-mcp"
        patched_server.bridge_cls.assert_called_once_with(mock_settings.instagram_bridge_url)
        patched_server.bridge.self_user_id.assert_called_once()

    def test_starts_event_stream_thread(
        self, patched_server: SimpleNamespace, mock_settings
    ) -> None:
        create_server(mock_settings)

        patched_server.thread_cls.assert_called_once()
        _, kwargs = patched_server.thread_cls.call_args
        assert kwargs["target"] is patched_server.stream
        assert kwargs["name"] == "bridge-events"
        assert kwargs["daemon"] is True
        # The bridge URL is the first stream_events arg.
        assert kwargs["args"][0] == mock_settings.instagram_bridge_url
        patched_server.thread_cls.return_value.start.assert_called_once()

    def test_registers_channel_and_tools(
        self, patched_server: SimpleNamespace, mock_settings
    ) -> None:
        mcp = create_server(mock_settings)

        tools = {tool.name for tool in mcp._tool_manager._tools.values()}
        assert {"subscribe", "reply", "list_threads", "share_media"} <= tools
        assert mcp.instructions is not None
        assert "Instagram DM channel" in mcp.instructions

    def test_loads_settings_when_none(self, patched_server: SimpleNamespace, mock_settings) -> None:
        with patch("instagram_mcp.server.get_settings", return_value=mock_settings) as get_settings:
            create_server()
            get_settings.assert_called_once()

    def test_subscribes_from_settings(self, patched_server: SimpleNamespace, mock_settings) -> None:
        settings = mock_settings.model_copy(
            update={"instagram_subscribe": "ly=111,bad=222", "instagram_control_thread": "999"}
        )
        create_server(settings)

        aliases = {sub.split(" ")[0] for sub in server._channel.subscriptions()}
        assert aliases == {"ly", "bad", "control"}


class TestGetMcp:
    def test_get_mcp_not_initialized(self) -> None:
        server._mcp = None
        with pytest.raises(RuntimeError, match="not initialized"):
            get_mcp()

    def test_get_mcp_initialized(self, patched_server: SimpleNamespace, mock_settings) -> None:
        mcp = create_server(mock_settings)
        assert get_mcp() is mcp


class TestGetBridge:
    def test_get_bridge_not_initialized(self) -> None:
        server._bridge = None
        with pytest.raises(RuntimeError, match="not initialized"):
            get_bridge()

    def test_get_bridge_initialized(self, patched_server: SimpleNamespace, mock_settings) -> None:
        create_server(mock_settings)
        assert get_bridge() is patched_server.bridge


class TestMain:
    def test_main_runs_stdio(self) -> None:
        mock_mcp = MagicMock()
        with patch("instagram_mcp.server.create_server", return_value=mock_mcp):
            main()
        mock_mcp.run.assert_called_once_with(transport="stdio")

    def test_main_keyboard_interrupt_stops_events(self) -> None:
        server._stop_events.clear()
        mock_mcp = MagicMock()
        mock_mcp.run.side_effect = KeyboardInterrupt()
        with patch("instagram_mcp.server.create_server", return_value=mock_mcp):
            main()  # must not raise
        assert server._stop_events.is_set()
