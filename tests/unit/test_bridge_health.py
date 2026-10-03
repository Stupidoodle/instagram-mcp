"""Probes: /health stays as the thin client reads it, /ready follows the Instagram session."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import httpx2
import pytest

from instagram_mcp import bridge

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest.fixture
def gateway() -> MagicMock:
    g = MagicMock(self_user_id="1000")
    g.mqtt_connected.return_value = True
    return g


@pytest.fixture
async def http(gateway: MagicMock) -> AsyncIterator[httpx2.AsyncClient]:
    with patch.object(bridge, "gateway", gateway):
        transport = httpx2.ASGITransport(app=bridge.build_app(), raise_app_exceptions=False)
        async with httpx2.AsyncClient(transport=transport, base_url="http://bridge") as client:
            yield client


async def test_ready_while_logged_in_and_connected(http: httpx2.AsyncClient) -> None:
    resp = await http.get("/ready")
    assert (resp.status_code, resp.json()) == (200, {"ready": True})


async def test_not_ready_while_mqtt_is_down(http: httpx2.AsyncClient, gateway: MagicMock) -> None:
    gateway.mqtt_connected.return_value = False
    resp = await http.get("/ready")
    assert (resp.status_code, resp.json()) == (503, {"ready": False, "reason": "disconnected"})


async def test_not_ready_before_the_login(http: httpx2.AsyncClient, gateway: MagicMock) -> None:
    gateway.self_user_id = ""
    resp = await http.get("/ready")
    assert (resp.status_code, resp.json()) == (503, {"ready": False, "reason": "not_logged_in"})


async def test_health_is_unchanged(http: httpx2.AsyncClient, gateway: MagicMock) -> None:
    gateway.mqtt_connected.return_value = False
    resp = await http.get("/health")
    assert (resp.status_code, resp.json()) == (
        200,
        {"ok": True, "self_user_id": "1000", "mqtt_connected": False},
    )
