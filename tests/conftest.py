"""Shared pytest fixtures for Instagram MCP Server tests.

The MCP process is a thin client of the instagram-bridge daemon: it opens no
Instagram connection of its own. Tool unit tests therefore drive a mocked
``BridgeClient`` (see ``mock_bridge``); only ``test_client.py`` still exercises
the real ``InstagramClient`` (which now lives inside the bridge), so the
instagrapi-shaped fixtures below are kept for it.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

# No test may export to a real collector or pick up a persona folder from the shell.
for _name in [n for n in os.environ if n.startswith("OTEL_") or n == "DM_PERSONA_DIR"]:
    del os.environ[_name]

import pytest  # noqa: E402
from mcp.server.mcpserver import MCPServer  # noqa: E402
from opentelemetry import _logs, metrics, trace  # noqa: E402
from opentelemetry.sdk._logs import LoggerProvider  # noqa: E402
from opentelemetry.sdk._logs.export import (  # noqa: E402
    InMemoryLogRecordExporter,
    SimpleLogRecordProcessor,
)
from opentelemetry.sdk.metrics import MeterProvider  # noqa: E402
from opentelemetry.sdk.metrics.export import InMemoryMetricReader  # noqa: E402
from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: E402
    InMemorySpanExporter,
)

from instagram_mcp.bridge_client import BridgeClient  # noqa: E402
from instagram_mcp.client import InstagramClient  # noqa: E402
from instagram_mcp.config import Settings  # noqa: E402
from tests.support.telemetry import Telemetry  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Iterator


def _install_telemetry() -> Telemetry:
    """Global in-memory providers, installed once for the whole session.

    Installed when the suite starts, not when first asked, so every test sees the same
    tracer state whatever the order (the bridge adds a traceparent only while tracing).
    """
    spans, logs = InMemorySpanExporter(), InMemoryLogRecordExporter()
    reader = InMemoryMetricReader()
    tracer_provider = TracerProvider(shutdown_on_exit=False)
    tracer_provider.add_span_processor(SimpleSpanProcessor(spans))
    logger_provider = LoggerProvider(shutdown_on_exit=False)
    logger_provider.add_log_record_processor(SimpleLogRecordProcessor(logs))
    trace.set_tracer_provider(tracer_provider)
    metrics.set_meter_provider(MeterProvider(metric_readers=[reader], shutdown_on_exit=False))
    _logs.set_logger_provider(logger_provider)
    return Telemetry(spans, reader, logs)


_TELEMETRY = _install_telemetry()


@pytest.fixture
def restore_logging() -> Iterator[None]:
    """Put the root and package loggers back the way the test found them."""
    root, package = logging.getLogger(), logging.getLogger("instagram_mcp")
    handlers, level, package_level = root.handlers[:], root.level, package.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)
    package.setLevel(package_level)


@pytest.fixture
def telemetry() -> Iterator[Telemetry]:
    """The recorded spans and logs (this test's only) and metrics (the session's)."""
    _TELEMETRY.clear()
    yield _TELEMETRY
    _TELEMETRY.clear()


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
    msg.link = None
    msg.reactions = None
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
