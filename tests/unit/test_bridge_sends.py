"""Sends through the bridge: an instagram.send CLIENT span and a duration per platform call."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import httpx2
import pytest
from opentelemetry.trace import SpanKind, StatusCode

from instagram_mcp import bridge
from instagram_mcp.instruments import SEND_BUCKETS

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from tests.support.telemetry import Telemetry

THREAD = "340282366841700000000000000000000000001"
PARENT = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
SENT = SimpleNamespace(message_id="m1")


@pytest.fixture
def client_mock() -> MagicMock:
    client = MagicMock()
    for name in ("reply_to_thread", "send_photo", "send_video", "send_voice"):
        getattr(client, name).return_value = SENT
    client.send_disappearing.return_value = "m2"
    client.react.return_value = True
    client.share_media.return_value = True
    client.share_profile.return_value = True
    return client


@pytest.fixture
async def http(client_mock: MagicMock) -> AsyncIterator[httpx2.AsyncClient]:
    with patch.object(bridge, "gateway", MagicMock(client=client_mock)):
        transport = httpx2.ASGITransport(app=bridge.build_app(), raise_app_exceptions=False)
        async with httpx2.AsyncClient(transport=transport, base_url="http://bridge") as client:
            yield client


def _duration(telemetry: Telemetry, kind: str, outcome: str) -> float:
    return telemetry.total(
        "dm.bridge.send.duration", platform="instagram", kind=kind, outcome=outcome
    )


async def test_a_text_send(http: httpx2.AsyncClient, telemetry: Telemetry) -> None:
    before = _duration(telemetry, "text", "ok")
    body = {"thread_id": THREAD, "text": "hallo zäme"}
    resp = await http.post("/send", json=body, headers={"traceparent": PARENT})
    assert resp.json() == {"success": True, "message_id": "m1"}
    (send,) = telemetry.named("instagram.send")
    (server,) = telemetry.named("POST /send")
    assert send.kind is SpanKind.CLIENT
    assert send.parent is not None and send.parent.span_id == server.context.span_id
    assert dict(send.attributes or {}) == {
        "dm.platform": "instagram",
        "dm.message.kind": "text",
        "dm.outcome": "ok",
        "dm.message.bytes": len("hallo zäme".encode()),
    }
    assert _duration(telemetry, "text", "ok") == before + 1
    metric = telemetry.metric("dm.bridge.send.duration")
    assert metric is not None and metric.unit == "s"
    assert tuple(metric.data.data_points[0].explicit_bounds) == SEND_BUCKETS  # type: ignore[union-attr]


async def test_an_unconfirmed_send_is_an_error(
    http: httpx2.AsyncClient, client_mock: MagicMock, telemetry: Telemetry
) -> None:
    client_mock.reply_to_thread.return_value = None
    before = _duration(telemetry, "text", "error")
    resp = await http.post("/send", json={"thread_id": THREAD, "text": "x"})
    assert resp.status_code == 502
    (send,) = telemetry.named("instagram.send")
    assert send.status.status_code is StatusCode.ERROR
    assert (send.attributes or {})["error.type"] == "not_confirmed"
    assert _duration(telemetry, "text", "error") == before + 1


async def test_a_crashed_send_is_an_error_without_its_message(
    http: httpx2.AsyncClient, client_mock: MagicMock, telemetry: Telemetry
) -> None:
    client_mock.reply_to_thread.side_effect = RuntimeError(f"/direct_v2/threads/{THREAD}/")
    before = _duration(telemetry, "text", "error")
    resp = await http.post("/send", json={"thread_id": THREAD, "text": "x"})
    assert resp.status_code == 500
    (send,) = telemetry.named("instagram.send")
    assert dict(send.attributes or {})["error.type"] == "RuntimeError"
    assert send.status.description is None and send.events == ()
    assert _duration(telemetry, "text", "error") == before + 1


FILE_SENDS = [
    ("/send_media", {"kind": "photo"}, "image", "send_photo"),
    ("/send_media", {"kind": "video"}, "video", "send_video"),
    ("/send_media", {"kind": "photo", "view_mode": "once"}, "image", "send_disappearing"),
    ("/send_voice", {}, "audio", "send_voice"),
]


@pytest.mark.parametrize("case", FILE_SENDS, ids=[c[3] for c in FILE_SENDS])
async def test_file_sends_carry_their_size(
    http: httpx2.AsyncClient,
    client_mock: MagicMock,
    telemetry: Telemetry,
    tmp_path: Path,
    case: tuple[str, dict[str, Any], str, str],
) -> None:
    route, body, kind, call = case
    media = tmp_path / "file.bin"
    media.write_bytes(b"x" * 1234)
    before = _duration(telemetry, kind, "ok")
    resp = await http.post(route, json={"thread_id": THREAD, "path": str(media)} | body)
    assert resp.json()["success"] is True
    getattr(client_mock, call).assert_called_once()
    (send,) = telemetry.named("instagram.send")
    assert (send.attributes or {})["dm.message.kind"] == kind
    assert (send.attributes or {})["dm.message.bytes"] == 1234
    assert _duration(telemetry, kind, "ok") == before + 1


async def test_a_missing_file_has_no_size(http: httpx2.AsyncClient, telemetry: Telemetry) -> None:
    await http.post("/send_voice", json={"thread_id": THREAD, "path": "/nope/a.m4a"})
    (send,) = telemetry.named("instagram.send")
    assert "dm.message.bytes" not in (send.attributes or {})


@pytest.mark.parametrize(
    ("route", "body", "kind"),
    [
        ("/react", {"message_id": "i1", "emoji": "🔥"}, "reaction"),
        ("/share_media", {"media_id": "123"}, "share"),
        ("/share_profile", {"user_id": "42"}, "share"),
    ],
)
async def test_reactions_and_shares(
    http: httpx2.AsyncClient, telemetry: Telemetry, route: str, body: dict[str, Any], kind: str
) -> None:
    before = _duration(telemetry, kind, "ok")
    resp = await http.post(route, json={"thread_id": THREAD} | body)
    assert resp.json() == {"success": True}
    assert _duration(telemetry, kind, "ok") == before + 1
