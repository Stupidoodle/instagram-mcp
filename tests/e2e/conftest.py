"""E2E test fixtures — real Instagram accounts and MQTT connections.

Requires:
    - .instagram_session (bot1 session file)
    - TEST_USERNAME + TEST_PASSWORD env vars (bot2 credentials)

Run with: uv run pytest tests/e2e/ -m e2e -v
"""

from __future__ import annotations

import os
import queue
import time
from pathlib import Path

import pytest

from instagram_mcp.client import InstagramClient
from instagram_mcp.mqtt.manager import MQTTManager

# Session file paths (resolved to project root)
BOT1_SESSION = Path(".instagram_session").resolve()
BOT2_SESSION = Path(".instagram_session_bot2").resolve()


def _load_dotenv() -> None:
    """Load .env into os.environ (only sets missing keys)."""
    env_file = Path(".env").resolve()
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        if key and value:
            os.environ.setdefault(key.strip(), value.strip())


# Load .env before fixtures run
_load_dotenv()


# ---------------------------------------------------------------------------
# Session-scoped fixtures (expensive, created once per test run)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def bot1_client() -> InstagramClient:
    """Main account client loaded from existing session."""
    if not BOT1_SESSION.exists():
        pytest.skip("Bot1 session (.instagram_session) not found")
    client = InstagramClient(session_file=BOT1_SESSION)
    if not client.load_session():
        pytest.skip("Bot1 session failed to load")
    return client


@pytest.fixture(scope="session")
def bot2_client() -> InstagramClient:
    """Test account client (login or load session)."""
    username = os.environ.get("TEST_USERNAME")
    password = os.environ.get("TEST_PASSWORD")
    if not username or not password:
        pytest.skip("TEST_USERNAME/TEST_PASSWORD not set")
    client = InstagramClient(session_file=BOT2_SESSION)
    client.login_or_load_session(username=username, password=password)
    return client


@pytest.fixture(scope="session")
def bot1_user_id(bot1_client: InstagramClient) -> str:
    """Bot1's Instagram user ID."""
    return str(bot1_client.client.user_id)


@pytest.fixture(scope="session")
def bot2_user_id(bot2_client: InstagramClient) -> str:
    """Bot2's Instagram user ID."""
    return str(bot2_client.client.user_id)


@pytest.fixture(scope="session")
def bot2_username() -> str:
    """Bot2's Instagram username."""
    return os.environ["TEST_USERNAME"]


@pytest.fixture(scope="session")
def shared_thread_id(bot1_client: InstagramClient, bot2_user_id: str) -> str:
    """DM thread between bot1 and bot2 (created if needed)."""
    msg = bot1_client.send_message(text="e2e test init", user_ids=[bot2_user_id])
    if msg is None:
        pytest.skip("Failed to create thread between bot1 and bot2")
    time.sleep(2)  # Let Instagram propagate
    return msg.thread_id


@pytest.fixture(scope="session")
def mqtt_manager(bot1_client: InstagramClient) -> MQTTManager:
    """Connected MQTT manager for bot1 (session-scoped)."""
    iris = bot1_client.get_iris_info()
    mqtt = MQTTManager()
    mqtt.connect(
        session_file=BOT1_SESSION,
        seq_id=iris["seq_id"],
        snapshot_at_ms=iris["snapshot_at_ms"],
        app_version=iris["app_version"],
    )
    time.sleep(2)  # Let Iris subscription settle
    yield mqtt
    mqtt.disconnect()


# ---------------------------------------------------------------------------
# Per-test helpers
# ---------------------------------------------------------------------------


def drain_queue(q: queue.SimpleQueue, timeout: float = 1.0) -> list:
    """Drain all pending events from a queue (to clear stale replay buffer)."""
    events = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            events.append(q.get(timeout=0.3))
        except queue.Empty:
            break
    return events


@pytest.fixture(autouse=True)
def _rate_limit_pause():
    """Pause between tests to respect Instagram rate limits."""
    yield
    time.sleep(1)
