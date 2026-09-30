"""The thin client continues the bridge's trace and never hands its traceparent to Claude Code.

Each path that reaches Claude Code is covered: live events, events replayed after
Last-Event-ID, and messages backfilled from history.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import httpx2
from opentelemetry import trace
from opentelemetry.trace import SpanKind

from instagram_mcp import instruments
from instagram_mcp.bridge_client import BridgeClient, _dispatch, sse_frames, stream_events
from instagram_mcp.catch_up import CatchUp, ChannelState
from instagram_mcp.channel import Channel

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from tests.support.telemetry import Telemetry

ME, HER = "1000", 5550001234
THREAD = "340282366841700000000000000000000000077"
TRACE = "0af7651916cd43dd8448eb211c80319c"


def _traceparent(span_id: int) -> str:
    return f"00-{TRACE}-{span_id:016x}-01"


def _message(item_id: str, traceparent: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": "message",
        "thread_id": THREAD,
        "item_id": item_id,
        "user_id": str(HER),
        "text": "hallo",
        "item_type": "text",
        "timestamp": 1_770_000_000_000_000,
    }
    if traceparent:
        payload["traceparent"] = traceparent
    return payload


def _frame(event_id: str, payload: dict[str, Any]) -> bytes:
    return f"id: {event_id}\ndata: {json.dumps(payload)}\n\n".encode()


def _hello(seq: int, gap: bool = False) -> bytes:
    data = json.dumps({"boot": "b1", "seq": seq, "gap": gap})
    return f"event: hello\ndata: {data}\n\n".encode()


class Persona:
    """A subscribed channel whose pushes are held, a catch-up in front of it."""

    def __init__(self, tmp_path: Path, history: list[dict[str, Any]] | None = None) -> None:
        self.channel = Channel(self_user_id=ME, describe_thread=lambda _t: ("Alex", {}))
        self.channel.subscribe(THREAD, "alex")
        self.state = ChannelState(tmp_path / "state.json")
        self.catch_up = CatchUp(
            self.state,
            lambda _t, _n: history or [],
            self.channel.thread_ids,
            self.channel.handle,
        )
        self.requests: list[httpx2.Request] = []

    def stream(self, body: bytes, until: int) -> None:
        def handler(request: httpx2.Request) -> httpx2.Response:
            self.requests.append(request)
            return httpx2.Response(200, content=body)

        stream_events(
            "http://bridge",
            self.channel.handle,
            lambda: len(self.channel._pending) >= until or len(self.requests) >= 3,
            catch_up=self.catch_up,
            transport=httpx2.MockTransport(handler),
        )

    async def notifications(self) -> list[dict[str, Any]]:
        """Write the held pushes to a fake Claude Code; what it received."""
        session = MagicMock()
        session.send_notification = AsyncMock()
        self.channel._session = session
        while self.channel._pending:
            await self.channel._send(*self.channel._pending.popleft())
        return [call.args[0].params for call in session.send_notification.await_args_list]


def _assert_stripped(notifications: list[dict[str, Any]]) -> None:
    assert notifications
    for params in notifications:
        text = json.dumps(params)
        assert "traceparent" not in text
        assert TRACE not in text


def _delivers(telemetry: Telemetry) -> list[Any]:
    return telemetry.named("dm.channel.deliver")


async def test_live_events_continue_the_trace_and_are_stripped(
    tmp_path: Path, telemetry: Telemetry, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(instruments, "persona", "mini")
    persona = Persona(tmp_path)
    body = _hello(0) + _frame("b1-1", _message("101", _traceparent(0xAB)))
    persona.stream(body, until=1)
    _assert_stripped(await persona.notifications())
    (deliver,) = _delivers(telemetry)
    assert deliver.kind is SpanKind.CONSUMER
    assert deliver.parent is not None
    assert (deliver.parent.trace_id, deliver.parent.span_id) == (int(TRACE, 16), 0xAB)
    assert deliver.parent.is_remote
    assert dict(deliver.attributes or {}) == {
        "dm.platform": "instagram",
        "dm.persona": "mini",
        "dm.event.type": "message",
        "dm.delivery": "live",
        "messaging.message.id": "101",
    }


async def test_replayed_events_are_stripped_too(tmp_path: Path, telemetry: Telemetry) -> None:
    persona = Persona(tmp_path)
    persona.state.record(event_id="b1-1", thread_id=THREAD, item_id="100")
    body = (
        _hello(3)
        + _frame("b1-2", _message("101", _traceparent(0x02)))
        + _frame("b1-3", _message("102", _traceparent(0x03)))
    )
    persona.stream(body, until=2)
    assert persona.requests[0].headers["last-event-id"] == "b1-1"
    _assert_stripped(await persona.notifications())
    delivers = _delivers(telemetry)
    assert [dict(s.attributes or {})["dm.delivery"] for s in delivers] == ["replay", "replay"]
    assert [s.parent.span_id for s in delivers if s.parent] == [0x02, 0x03]


async def test_backfilled_history_never_carries_one(tmp_path: Path, telemetry: Telemetry) -> None:
    history = [
        {
            "message_id": "101",
            "media_type": "text",
            "text": "while you were away",
            "timestamp": "2026-09-30T02:00:00+02:00",
            "traceparent": _traceparent(0x09),  # never sent, but never passed on either
        }
    ]
    persona = Persona(tmp_path, history)
    persona.state.record(thread_id=THREAD, item_id="100")
    persona.stream(_hello(0, gap=True), until=1)
    notifications = await persona.notifications()
    _assert_stripped(notifications)
    assert notifications[0]["meta"]["backfilled"] == "true"
    assert _delivers(telemetry) == []


def test_an_event_without_a_traceparent_starts_its_own_trace(telemetry: Telemetry) -> None:
    got: list[object] = []
    (frame,) = sse_frames([f"data: {json.dumps(_message('1'))}", ""])
    _dispatch(frame, got.append, None)
    (deliver,) = _delivers(telemetry)
    assert deliver.parent is None
    assert len(got) == 1


def test_frames_that_are_not_objects_are_ignored(telemetry: Telemetry) -> None:
    got: list[object] = []
    for frame in sse_frames(["data: [1, 2]", "", "data: not json", ""]):
        _dispatch(frame, got.append, None)
    assert got == []
    assert _delivers(telemetry) == []


class TestInjection:
    def _bridge(self, seen: list[httpx2.Request]) -> BridgeClient:
        def handler(request: httpx2.Request) -> httpx2.Response:
            seen.append(request)
            return httpx2.Response(200, json={"success": True})

        return BridgeClient("http://bridge", transport=httpx2.MockTransport(handler))

    def test_calls_inside_a_tool_call_carry_its_trace(self, telemetry: Telemetry) -> None:
        seen: list[httpx2.Request] = []
        bridge = self._bridge(seen)
        with trace.get_tracer("t").start_as_current_span("tools/call reply") as span:
            bridge.send(THREAD, "hi")
            bridge.threads()
        ctx = span.get_span_context()
        expected = f"00-{ctx.trace_id:032x}-{ctx.span_id:016x}-{int(ctx.trace_flags):02x}"
        assert [r.headers["traceparent"] for r in seen] == [expected, expected]

    def test_no_trace_no_header(self) -> None:
        seen: list[httpx2.Request] = []
        self._bridge(seen).send(THREAD, "hi")
        assert "traceparent" not in seen[0].headers
