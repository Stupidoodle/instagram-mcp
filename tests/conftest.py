"""Shared pytest fixtures for Instagram MCP Server tests.

The MCP process is a thin client of the instagram-bridge daemon: it opens no
Instagram connection of its own. Tool unit tests therefore drive a mocked
``BridgeClient`` (see ``mock_bridge``); only ``test_client.py`` still exercises
the real ``InstagramClient`` (which now lives inside the bridge), so the
instagrapi-shaped fixtures below are kept for it.
"""

from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from mcp.server.mcpserver import MCPServer

from instagram_mcp.bridge_client import BridgeClient
from instagram_mcp.client import InstagramClient
from instagram_mcp.config import Settings


@pytest.fixture
def mock_settings() -> Settings:
    """Create mock settings for testing."""
    with patch.dict(
        "os.environ",
        {
            "INSTAGRAM_USERNAME": "test_user",
            "INSTAGRAM_PASSWORD": "test_pass",
        },
    ):
        return Settings(
            instagram_username="test_user",
            instagram_password="test_pass",  # type: ignore[arg-type]
            instagram_session_file=Path("/tmp/test_session"),
            log_level="DEBUG",
        )


@pytest.fixture
def mock_bridge() -> MagicMock:
    """A mocked BridgeClient the thin-client tools talk to instead of Instagram."""
    return MagicMock(spec=BridgeClient)


@pytest.fixture
def mock_mcp() -> MCPServer:
    """Create an MCP server for testing tools."""
    return MCPServer("test-server")


# ── instagrapi-shaped fixtures (used by test_client.py) ──────────────────────


@pytest.fixture
def mock_ig_user() -> MagicMock:
    """Create a mock instagrapi User object."""
    user = MagicMock()
    user.pk = 123456
    user.username = "test_user"
    user.full_name = "Test User"
    user.profile_pic_url = "https://example.com/pic.jpg"
    user.is_verified = False
    return user


@pytest.fixture
def mock_ig_message(mock_ig_user: MagicMock) -> MagicMock:
    """Create a mock instagrapi DirectMessage object."""
    msg = MagicMock()
    msg.id = "111111111"
    msg.text = "Hello, this is a test message!"
    msg.user = mock_ig_user
    msg.user_id = 123456  # Same as mock_ig_user.pk
    msg.timestamp = datetime(2024, 1, 15, 10, 30, 0)
    msg.is_sent_by_viewer = True
    msg.item_type = "text"
    msg.media = None
    return msg


@pytest.fixture
def mock_ig_thread(mock_ig_user: MagicMock, mock_ig_message: MagicMock) -> MagicMock:
    """Create a mock instagrapi DirectThread object."""
    thread = MagicMock()
    thread.id = "123456789"
    thread.thread_title = "Test Conversation"
    thread.users = [mock_ig_user]
    thread.last_activity_at = datetime(2024, 1, 15, 10, 35, 0)
    thread.is_group = False
    thread.muted = False
    thread.read_state = 1  # read_state != 0 means unread
    thread.messages = [mock_ig_message]
    return thread


@pytest.fixture
def mock_instagrapi_client() -> MagicMock:
    """Create a mock instagrapi Client."""
    client = MagicMock()
    client.login = MagicMock(return_value=True)
    client.login_by_sessionid = MagicMock(return_value=True)
    client.get_settings = MagicMock(return_value={"authorization_data": {"sessionid": "test"}})
    client.set_settings = MagicMock()
    return client


@pytest.fixture
def instagram_client(mock_instagrapi_client: MagicMock, tmp_path: Path) -> InstagramClient:
    """Create an InstagramClient with mocked instagrapi Client."""
    with patch("instagram_mcp.client.Client", return_value=mock_instagrapi_client):
        client = InstagramClient(session_file=tmp_path / "test_session")
        client.client = mock_instagrapi_client
        client._logged_in = True
        return client


def create_mock_tool_context() -> dict[str, Any]:
    """Create a mock context for tool testing."""
    return {}
