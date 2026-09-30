"""MQTT connection events and the connected gauge."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from instagram_mcp import instruments
from instagram_mcp.bridge import Gateway
from instagram_mcp.mqtt.connection import MQTToTConnection
from instagram_mcp.mqtt.manager import MQTTManager

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from tests.support.telemetry import Telemetry

EVENTS = "dm.bridge.connection.events"


def _counts(telemetry: Telemetry) -> Callable[[], dict[str, float]]:
    names = ("connected", "disconnected", "reconnect", "connect_failed", "stream_error")
    before = {e: telemetry.total(EVENTS, event=e, platform="instagram") for e in names}

    def delta() -> dict[str, float]:
        now = {e: telemetry.total(EVENTS, event=e, platform="instagram") for e in names}
        return {e: now[e] - before[e] for e in names if now[e] != before[e]}

    return delta


def _socket(*chunks: bytes) -> MagicMock:
    sock = MagicMock()
    sock.recv.side_effect = list(chunks)
    return sock


@patch("instagram_mcp.mqtt.connection.socket.create_connection")
@patch("instagram_mcp.mqtt.connection.ssl.create_default_context")
class TestConnection:
    def test_connected_then_disconnected(
        self, ssl_ctx: MagicMock, _create: MagicMock, telemetry: Telemetry
    ) -> None:
        delta = _counts(telemetry)
        ssl_ctx.return_value.wrap_socket.return_value = _socket(b"\x20", b"\x02", b"\x00\x00")
        conn = MQTToTConnection()
        conn.connect(b"payload")
        conn.disconnect()
        conn.disconnect()  # already closed: not counted again
        assert delta() == {"connected": 1, "disconnected": 1}

    def test_a_rejected_connect_is_not_a_disconnect(
        self, ssl_ctx: MagicMock, _create: MagicMock, telemetry: Telemetry
    ) -> None:
        delta = _counts(telemetry)
        ssl_ctx.return_value.wrap_socket.return_value = _socket(b"\x20", b"\x02", b"\x00\x05")
        with pytest.raises(RuntimeError, match="rejected"):
            MQTToTConnection().connect(b"payload")
        assert delta() == {}


@pytest.mark.parametrize(
    "chunks", [(b"",), (b"\x30", b""), (b"\x30", b"\x05", b"")], ids=["header", "length", "body"]
)
def test_the_remote_closing_is_a_disconnect(
    chunks: tuple[bytes, ...], telemetry: Telemetry
) -> None:
    delta = _counts(telemetry)
    conn = MQTToTConnection()
    conn._sock = _socket(*chunks)
    conn.read_packet()
    assert not conn.is_connected
    assert delta() == {"disconnected": 1}


class TestManager:
    def test_a_failed_first_connect(self, tmp_path: Path, telemetry: Telemetry) -> None:
        delta = _counts(telemetry)
        mgr = MQTTManager()
        with pytest.raises(FileNotFoundError):
            mgr.connect(tmp_path / "missing.json", seq_id=1)
        assert delta() == {"connect_failed": 1}

    def test_the_watchdog_reconnects(self, tmp_path: Path, telemetry: Telemetry) -> None:
        delta = _counts(telemetry)
        mgr = MQTTManager()
        mgr._conn = MagicMock(is_connected=False)
        mgr._session_file = tmp_path / "session.json"
        with (
            patch.object(mgr, "_do_connect"),
            patch("instagram_mcp.mqtt.manager.time.sleep"),
        ):
            assert mgr.ensure_connected() is True
        with patch.object(mgr, "_do_connect", side_effect=OSError("down")):
            assert mgr.ensure_connected() is False
        assert delta() == {"reconnect": 1, "connect_failed": 1}

    def test_a_crashed_reader_is_a_stream_error(self, telemetry: Telemetry) -> None:
        delta = _counts(telemetry)
        mgr = MQTTManager()
        mgr._conn = MagicMock(is_connected=True)
        mgr._conn.read_packet.side_effect = ConnectionResetError
        mgr._reader_loop()
        assert delta() == {"stream_error": 1}


def test_the_connected_gauge(telemetry: Telemetry) -> None:
    gateway = Gateway.__new__(Gateway)
    gateway.mqtt = None
    instruments.sources.bridge_connected = gateway.mqtt_connected
    try:
        assert telemetry.points("dm.bridge.connected") == [{"platform": "instagram"}]
        assert telemetry.total("dm.bridge.connected") == 0
        gateway.mqtt = MagicMock(is_connected=True)
        assert telemetry.total("dm.bridge.connected", platform="instagram") == 1
    finally:
        instruments.sources.bridge_connected = None
    assert telemetry.points("dm.bridge.connected") == []
