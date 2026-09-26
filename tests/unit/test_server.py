"""Unit tests for MCP server."""

from unittest.mock import MagicMock, patch

import pytest

from instagram_mcp.client import AuthenticationError, SessionError
from instagram_mcp.server import (
    create_server,
    get_client,
    get_mcp,
    main,
)


class TestCreateServer:
    def test_create_server_success(self, mock_settings: MagicMock) -> None:
        with (
            patch("instagram_mcp.server.get_settings", return_value=mock_settings),
            patch("instagram_mcp.server.setup_logging"),
            patch("instagram_mcp.server.InstagramClient") as mock_client_class,
        ):
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client

            mcp = create_server(mock_settings)

            assert mcp is not None
            assert mcp.name == "instagram-mcp"
            mock_client.login_or_load_session.assert_called_once()

    def test_create_server_auth_error(self, mock_settings: MagicMock) -> None:
        with (
            patch("instagram_mcp.server.get_settings", return_value=mock_settings),
            patch("instagram_mcp.server.setup_logging"),
            patch("instagram_mcp.server.InstagramClient") as mock_client_class,
            pytest.raises(AuthenticationError),
        ):
            mock_client = MagicMock()
            mock_client.login_or_load_session.side_effect = AuthenticationError("Failed")
            mock_client_class.return_value = mock_client

            create_server(mock_settings)

    def test_create_server_session_error(self, mock_settings: MagicMock) -> None:
        with (
            patch("instagram_mcp.server.get_settings", return_value=mock_settings),
            patch("instagram_mcp.server.setup_logging"),
            patch("instagram_mcp.server.InstagramClient") as mock_client_class,
            pytest.raises(SessionError),
        ):
            mock_client = MagicMock()
            mock_client.login_or_load_session.side_effect = SessionError("Failed")
            mock_client_class.return_value = mock_client

            create_server(mock_settings)

    def test_create_server_loads_settings(self) -> None:
        with (
            patch("instagram_mcp.server.get_settings") as mock_get_settings,
            patch("instagram_mcp.server.setup_logging"),
            patch("instagram_mcp.server.InstagramClient") as mock_client_class,
            patch.dict(
                "os.environ",
                {
                    "INSTAGRAM_USERNAME": "test",
                    "INSTAGRAM_PASSWORD": "test",
                },
            ),
        ):
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client

            create_server()

            mock_get_settings.assert_called_once()


class TestGetMcp:
    def test_get_mcp_not_initialized(self) -> None:
        # Reset global state
        import instagram_mcp.server

        instagram_mcp.server._mcp = None

        with pytest.raises(RuntimeError, match="not initialized"):
            get_mcp()

    def test_get_mcp_initialized(self, mock_settings: MagicMock) -> None:
        with (
            patch("instagram_mcp.server.get_settings", return_value=mock_settings),
            patch("instagram_mcp.server.setup_logging"),
            patch("instagram_mcp.server.InstagramClient") as mock_client_class,
        ):
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client

            mcp = create_server(mock_settings)
            result = get_mcp()

            assert result is mcp


class TestGetClient:
    def test_get_client_not_initialized(self) -> None:
        # Reset global state
        import instagram_mcp.server

        instagram_mcp.server._client = None

        with pytest.raises(RuntimeError, match="not initialized"):
            get_client()

    def test_get_client_initialized(self, mock_settings: MagicMock) -> None:
        with (
            patch("instagram_mcp.server.get_settings", return_value=mock_settings),
            patch("instagram_mcp.server.setup_logging"),
            patch("instagram_mcp.server.InstagramClient") as mock_client_class,
        ):
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client

            create_server(mock_settings)
            result = get_client()

            assert result is mock_client


class TestMain:
    def test_main_success(self, mock_settings: MagicMock) -> None:
        with (
            patch("instagram_mcp.server.create_server") as mock_create,
        ):
            mock_mcp = MagicMock()
            mock_create.return_value = mock_mcp

            main()

            mock_create.assert_called_once()
            mock_mcp.run.assert_called_once_with(transport="stdio")

    def test_main_auth_error(self, mock_settings: MagicMock) -> None:
        with (
            patch(
                "instagram_mcp.server.create_server",
                side_effect=AuthenticationError("Failed"),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

        assert exc_info.value.code == 1

    def test_main_session_error(self, mock_settings: MagicMock) -> None:
        with (
            patch(
                "instagram_mcp.server.create_server",
                side_effect=SessionError("Failed"),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

        assert exc_info.value.code == 1

    def test_main_keyboard_interrupt(self, mock_settings: MagicMock) -> None:
        with (
            patch(
                "instagram_mcp.server.create_server",
                side_effect=KeyboardInterrupt(),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

        assert exc_info.value.code == 0


class TestChannelWiring:
    def test_realtime_events_feed_the_channel(
        self, mock_settings: MagicMock, _no_live_mqtt: MagicMock
    ) -> None:
        settings = mock_settings.model_copy(
            update={"instagram_subscribe": "ly=111,bad=222", "instagram_control_thread": "999"}
        )
        with (
            patch("instagram_mcp.server.setup_logging"),
            patch("instagram_mcp.server.InstagramClient") as mock_client_class,
        ):
            mock_client = mock_client_class.return_value
            mock_client.client.user_id = 42
            mock_client.get_thread.return_value = MagicMock(thread_title="Ly", users=[])

            mcp = create_server(settings)

        manager = _no_live_mqtt.return_value
        manager.set_listener.assert_called_once()
        manager.start_watchdog.assert_called_once()
        assert "subscribe" in mcp._tool_manager._tools
        assert mcp.instructions is not None
        assert "Instagram DM channel" in mcp.instructions

    def test_mqtt_connect_failure_still_starts_watchdog(
        self, mock_settings: MagicMock, _no_live_mqtt: MagicMock
    ) -> None:
        _no_live_mqtt.return_value.connect.side_effect = OSError("no route")
        with (
            patch("instagram_mcp.server.setup_logging"),
            patch("instagram_mcp.server.InstagramClient"),
        ):
            create_server(mock_settings)
        _no_live_mqtt.return_value.start_watchdog.assert_called_once()

    def test_no_iris_info_means_no_realtime(
        self, mock_settings: MagicMock, _no_live_mqtt: MagicMock
    ) -> None:
        with (
            patch("instagram_mcp.server.setup_logging"),
            patch("instagram_mcp.server.InstagramClient") as mock_client_class,
        ):
            mock_client_class.return_value.get_iris_info.side_effect = RuntimeError("429")
            create_server(mock_settings)
        _no_live_mqtt.assert_not_called()
