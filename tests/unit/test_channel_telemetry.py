"""The thin client's dm.channel metrics: messages, notifications, stream, catch-up, queue."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx2
import pytest
from mcp.server.mcpserver import MCPServer

from instagram_mcp import instruments, server
from instagram_mcp.bridge_client import BridgeClient, stream_events, stream_status
from instagram_mcp.catch_up import CatchUp, ChannelState
from instagram_mcp.channel import Channel, notification_type
from instagram_mcp.mqtt.events import MessageEvent, ReactionEvent
from instagram_mcp.tools.channel import register_messaging_tools
from instagram_mcp.tools.media import register_media_tools

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from instagram_mcp.config import Settings
    from tests.support.telemetry import Telemetry

ME, HER = "1000", 5550001234
THREAD = "340282366841700000000000000000000000077"


@pytest.fixture(autouse=True)
def persona(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(instruments, "persona", "mini")


def _delta(telemetry: Telemetry, name: str, **labels: str) -> Callable[[], float]:
    labels = {"persona": "mini", "platform": "instagram"} | labels
    before = telemetry.total(name, **labels)
    return lambda: telemetry.total(name, **labels) - before


def _channel(fail: bool = False) -> Channel:
    channel = Channel(self_user_id=ME, describe_thread=lambda _t: ("Alex", {}))
    channel.subscribe(THREAD, "alex")
    session = MagicMock()
    session.send_notification = AsyncMock(side_effect=RuntimeError if fail else None)
    channel._session = session
    return channel


def _message(**kwargs: Any) -> MessageEvent:
    fields = {"thread_id": THREAD, "item_id": "i1", "user_id": HER, "text": "hi"}
    return MessageEvent(**(fields | {"item_type": "text", "timestamp": 1} | kwargs))


class TestNotifications:
    async def test_their_message_counts_once_written(self, telemetry: Telemetry) -> None:
        messages = _delta(telemetry, "dm.channel.messages", direction="in", kind="audio")
        pushed = _delta(telemetry, "dm.channel.notifications", type="message", outcome="ok")
        channel = _channel()
        sent: list[int] = []
        channel.handle(_message(item_type="voice_media", transcript="x"), lambda: sent.append(1))
        content, meta, on_sent = channel._pending.popleft()
        await channel._send(content, meta, on_sent)
        assert (messages(), pushed(), sent) == (1, 1, [1])

    async def test_a_failed_push_counts_an_error_and_no_message(self, telemetry: Telemetry) -> None:
        messages = _delta(telemetry, "dm.channel.messages", direction="in", kind="text")
        failed = _delta(telemetry, "dm.channel.notifications", type="message", outcome="error")
        channel = _channel(fail=True)
        channel.handle(_message())
        content, meta, on_sent = channel._pending.popleft()
        await channel._send(content, meta, on_sent)
        assert (messages(), failed()) == (0, 1)

    async def test_own_and_reaction_events_are_not_their_messages(
        self, telemetry: Telemetry
    ) -> None:
        messages = _delta(telemetry, "dm.channel.messages", direction="in")
        channel = _channel()
        channel.handle(_message(user_id=int(ME), text="from my phone"))
        channel.handle(ReactionEvent(THREAD, "i1", HER, "emojis", "🔥"))
        for content, meta, on_sent in list(channel._pending):
            await channel._send(content, meta, on_sent)
        assert messages() == 0

    @pytest.mark.parametrize(
        ("meta", "kind"),
        [
            ({}, "message"),
            ({"media_error": "download failed"}, "media_failed"),
            ({"event_type": "edit"}, "message"),
            ({"event_type": "reaction"}, "reaction"),
            ({"event_type": "read"}, "receipt"),
            ({"event_type": "typing_stopped"}, "typing"),
            ({"event_type": "idle"}, "idle"),
            ({"event_type": "notice"}, "other"),
            ({"event_type": "command"}, "other"),
            ({"event_type": "unsend"}, "other"),
        ],
    )
    def test_types(self, meta: dict[str, str], kind: str) -> None:
        assert notification_type(meta) == kind

    def test_pending_queue_depth(self, telemetry: Telemetry) -> None:
        channel = Channel(self_user_id=ME, describe_thread=lambda _t: ("Alex", {}))
        channel.subscribe(THREAD, "alex")
        channel.handle(_message())
        instruments.sources.channel_queues = channel.queue_depths
        try:
            depth = telemetry.total("dm.channel.queue.depth", persona="mini", queue="pending")
            assert depth == 1
        finally:
            instruments.sources.channel_queues = None


class TestOut:
    def _tools(self, bridge: MagicMock) -> dict[str, Any]:
        mcp = MCPServer("t")
        channel = Channel(self_user_id=ME, describe_thread=lambda _t: ("Alex", {}))
        channel.subscribe(THREAD, "alex")
        register_messaging_tools(mcp, bridge, channel)
        register_media_tools(mcp, bridge)
        return {name: tool.fn for name, tool in mcp._tool_manager._tools.items()}

    def test_confirmed_sends_count_by_kind(self, telemetry: Telemetry, tmp_path: Path) -> None:
        kinds = ("text", "image", "video", "audio", "reaction", "share")
        deltas = {
            k: _delta(telemetry, "dm.channel.messages", direction="out", kind=k) for k in kinds
        }
        bridge = MagicMock(spec=BridgeClient)
        for name in ("send", "send_media", "send_voice", "react", "share_media", "share_profile"):
            getattr(bridge, name).return_value = {"success": True, "message_id": "m1"}
        tools = self._tools(bridge)
        tools["reply"](text="hi")
        tools["send_file"](file_path=str(tmp_path / "a.jpg"))
        tools["send_file"](file_path=str(tmp_path / "a.mp4"))
        tools["send_audio"](file_path=str(tmp_path / "a.m4a"))
        tools["react"](message_id="i1", emoji="🔥")
        tools["share_media"](media_id="1", thread_id=THREAD)
        tools["share_profile"](user_id="2", thread_id=THREAD)
        assert {k: d() for k, d in deltas.items()} == dict.fromkeys(kinds, 1) | {"share": 2}

    def test_failed_sends_do_not_count(self, telemetry: Telemetry) -> None:
        out = _delta(telemetry, "dm.channel.messages", direction="out")
        bridge = MagicMock(spec=BridgeClient)
        for name in ("send", "react", "share_media"):
            getattr(bridge, name).return_value = {"success": False, "error": "not confirmed"}
        tools = self._tools(bridge)
        tools["reply"](text="hi")
        tools["react"](message_id="i1", emoji="🔥")
        tools["share_media"](media_id="1", thread_id=THREAD)
        assert out() == 0


def _frame(event_id: str, item_id: str) -> bytes:
    message = {"type": "message", "thread_id": THREAD, "item_id": item_id, "user_id": str(HER)}
    return f"id: {event_id}\ndata: {json.dumps(message | {'item_type': 'text'})}\n\n".encode()


class TestStream:
    def test_reconnect_reasons_and_the_connected_gauge(self, telemetry: Telemetry) -> None:
        reasons = {
            r: _delta(telemetry, "dm.channel.stream.reconnects", reason=r)
            for r in ("ended", "http_status", "error")
        }
        responses = [httpx2.Response(200, content=_frame("b1-1", "i1")), httpx2.Response(503)]
        attempts: list[int] = []

        def handler(request: httpx2.Request) -> httpx2.Response:
            attempts.append(1)
            if len(attempts) > len(responses):
                raise httpx2.ConnectError("down", request=request)
            return responses[len(attempts) - 1]

        connected: list[bool] = []
        instruments.sources.stream_connected = lambda: stream_status.connected
        try:

            def on_event(_event: object) -> None:
                connected.append(
                    telemetry.total("dm.channel.stream.connected", persona="mini") == 1
                )

            with pytest.MonkeyPatch.context() as mp:
                mp.setattr("instagram_mcp.bridge_client.time.sleep", lambda _s: None)
                stream_events(
                    "http://bridge",
                    on_event,
                    lambda: len(attempts) >= 4,
                    transport=httpx2.MockTransport(handler),
                )
            assert connected == [True]
            assert telemetry.total("dm.channel.stream.connected", persona="mini") == 0
        finally:
            instruments.sources.stream_connected = None
        assert {r: d() for r, d in reasons.items()} == {"ended": 1, "http_status": 1, "error": 1}

    def test_replayed_events_are_counted(self, telemetry: Telemetry, tmp_path: Path) -> None:
        replay = _delta(telemetry, "dm.channel.catchup.events", mode="replay")
        body = (
            b'event: hello\ndata: {"boot": "b1", "seq": 6, "gap": false}\n\n'
            + _frame("b1-5", "100")
            + _frame("b1-6", "101")
            + _frame("b1-7", "102")
        )
        got: list[object] = []
        catch_up = CatchUp(
            ChannelState(tmp_path / "s.json"), MagicMock(), list, lambda e, _s: got.append(e)
        )
        stream_events(
            "http://bridge",
            got.append,
            lambda: len(got) >= 3,
            catch_up=catch_up,
            transport=httpx2.MockTransport(lambda _r: httpx2.Response(200, content=body)),
        )
        assert replay() == 2


def test_backfilled_events_are_counted(telemetry: Telemetry, tmp_path: Path) -> None:
    backfill = _delta(telemetry, "dm.channel.catchup.events", mode="backfill")
    state = ChannelState(tmp_path / "s.json")
    state.record(thread_id=THREAD, item_id="100")
    items = [
        {"message_id": str(n), "media_type": "text", "timestamp": "2026-09-30T02:00:00+02:00"}
        for n in (100, 101, 102)
    ]
    catch_up = CatchUp(state, lambda _t, _n: items, lambda: [THREAD], lambda _e, sent: sent())
    assert catch_up.fill_gap() == 2
    assert backfill() == 2


def test_the_thin_client_labels_its_persona(
    mock_settings: Settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DM_PERSONA_DIR", str(tmp_path / "christine-instagram"))
    monkeypatch.setattr(instruments, "sources", instruments.Sources())
    bridge = MagicMock()
    bridge.self_user_id.return_value = ME
    with (
        patch("instagram_mcp.server.setup_logging"),
        patch("instagram_mcp.server.BridgeClient", return_value=bridge),
        patch("instagram_mcp.server.threading.Thread"),
    ):
        server.create_server(mock_settings)
    assert instruments.persona == "christine"
    assert instruments.sources.stream_connected is not None
    assert instruments.sources.channel_queues is not None
    assert instruments.sources.channel_queues() == {"pending": 0}
