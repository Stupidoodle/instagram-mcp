"""The bridge's event pipeline: a CONSUMER span per MQTT event, counters, gauges, context."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import httpx2
import pytest
from opentelemetry import trace
from opentelemetry.trace import SpanKind, StatusCode

from instagram_mcp import instruments
from instagram_mcp.bridge import Gateway
from instagram_mcp.event_log import EventLog
from instagram_mcp.instruments import SEND_BUCKETS, message_kind
from instagram_mcp.media import InboundMedia
from instagram_mcp.mqtt.events import (
    Event,
    MessageEvent,
    ReactionEvent,
    SeenEvent,
    ThreadEvent,
    TypingEvent,
)
from instagram_mcp.shares import Share

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from opentelemetry.sdk.trace import ReadableSpan

    from tests.support.telemetry import Telemetry

ME = "1000"
HER = 5550001234
THREAD = "340282366841700000000000000000000000077"
TEXT = "see you at nine"
PRIVATE = (THREAD, str(HER), TEXT, "grüezi mitenand")


class Transcriber:
    """A stand-in for the transcriber service, recording the trace context it gets."""

    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.traceparents: list[str | None] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.traceparents.append(request.headers.get("traceparent"))
        if self.status != 200:
            return httpx2.Response(self.status, json={"error": "all engines failed"})
        return httpx2.Response(200, json={"text": "grüezi mitenand"})


def _gateway(tmp_path: Path) -> Gateway:
    g = Gateway.__new__(Gateway)  # no login: the event plumbing only
    g.settings = MagicMock(
        instagram_media_dir=tmp_path,
        instagram_ephemeral_dir=tmp_path / "eph",
        instagram_transcriber_url="http://127.0.0.1:8090",
    )
    g.client = MagicMock()
    g.client.download_message_media.side_effect = lambda _t, item, *_a: tmp_path / f"{item}.m4a"
    g.self_user_id = ME
    g.mqtt = None
    g.seen_log = None
    g._subscribers = set()
    g._loop = asyncio.get_running_loop()
    g.events = EventLog(10)
    g._media = InboundMedia(
        self_user_id=ME,
        download=g._download_media,
        transcribe=g._transcribe,
        describe_share=g._describe_share,
        deliver=g._deliver,
    )
    return g


@pytest.fixture
def transcriber() -> Iterator[Transcriber]:
    fake = Transcriber()
    real = httpx2.AsyncClient

    def client(**kwargs: Any) -> httpx2.AsyncClient:
        return real(transport=httpx2.MockTransport(fake), **kwargs)

    with patch("instagram_mcp.bridge.httpx2.AsyncClient", client):
        yield fake


async def _feed(g: Gateway, *events: Event) -> list[dict[str, Any]]:
    """Hand events over as the MQTT reader thread does; returns the SSE payloads."""
    q = g.add_subscriber()
    for event in events:
        await asyncio.to_thread(g._on_event, event)
    await asyncio.sleep(0)
    assert g._media is not None
    await g._media.drain()
    frames = [q.get_nowait() for _ in range(q.qsize())]
    return [json.loads(str(f).split("data: ", 1)[1]) for f in frames]


def _message(item_type: str = "text", user: int = HER, item_id: str = "i1") -> MessageEvent:
    return MessageEvent(THREAD, item_id, user, TEXT, item_type, 1_770_000_000_000_000)


def _traceparent(span: ReadableSpan) -> str:
    ctx = span.get_span_context()
    assert ctx is not None
    return f"00-{ctx.trace_id:032x}-{ctx.span_id:016x}-{int(ctx.trace_flags):02x}"


def _attributes(telemetry: Telemetry) -> str:
    return json.dumps([dict(s.attributes or {}) for s in telemetry.spans()])


class TestEventSpans:
    async def test_a_voice_note_is_one_trace(
        self, tmp_path: Path, telemetry: Telemetry, transcriber: Transcriber
    ) -> None:
        g = _gateway(tmp_path)
        (payload,) = await _feed(g, _message("voice_media"))
        (event,) = telemetry.named("instagram.event message")
        assert event.kind is SpanKind.CONSUMER
        assert dict(event.attributes or {}) == {
            "dm.platform": "instagram",
            "dm.event.type": "message",
            "dm.direction": "in",
            "messaging.message.id": "i1",
            "dm.message.kind": "audio",
        }
        (download,) = telemetry.named("instagram.media.download")
        (post,) = telemetry.named("POST /transcribe")
        assert download.parent is not None and download.parent.span_id == event.context.span_id
        assert post.parent is not None and post.parent.span_id == event.context.span_id
        assert post.kind is SpanKind.CLIENT
        assert (post.attributes or {})["http.response.status_code"] == 200
        assert transcriber.traceparents == [_traceparent(post)]
        assert payload["traceparent"] == _traceparent(event)
        assert payload["transcript"] == "grüezi mitenand"
        for secret in PRIVATE:
            assert secret not in _attributes(telemetry)

    async def test_a_failed_transcription_is_a_client_error(
        self, tmp_path: Path, telemetry: Telemetry, transcriber: Transcriber
    ) -> None:
        transcriber.status = 502
        g = _gateway(tmp_path)
        (payload,) = await _feed(g, _message("voice_media"))
        (post,) = telemetry.named("POST /transcribe")
        assert post.status.status_code is StatusCode.ERROR
        assert (post.attributes or {})["error.type"] == "502"
        assert payload["media_error"].startswith("transcription failed")

    async def test_a_failed_download_marks_its_span(
        self, tmp_path: Path, telemetry: Telemetry
    ) -> None:
        g = _gateway(tmp_path)
        g._media._download = MagicMock(side_effect=RuntimeError(THREAD))  # type: ignore[union-attr]
        await _feed(g, _message("media"))
        (download,) = telemetry.named("instagram.media.download")
        assert download.status.status_code is StatusCode.ERROR
        assert download.status.description is None
        assert (download.attributes or {})["error.type"] == "RuntimeError"
        assert THREAD not in _attributes(telemetry)

    async def test_every_event_type(self, tmp_path: Path, telemetry: Telemetry) -> None:
        g = _gateway(tmp_path)
        events: list[Event] = [
            ReactionEvent(THREAD, "i1", int(ME), "emojis", "🔥"),
            SeenEvent(THREAD, HER, "i1", 1),
            TypingEvent(THREAD, HER, 1, 10_000),
            ThreadEvent(THREAD, "add", "/x"),
        ]
        payloads = await _feed(g, *events)
        assert [p["type"] for p in payloads] == ["reaction", "read", "typing", "thread"]
        names = {s.name for s in telemetry.spans()}
        assert names == {f"instagram.event {t}" for t in ("reaction", "read", "typing", "thread")}
        (reaction,) = telemetry.named("instagram.event reaction")
        assert (reaction.attributes or {})["dm.direction"] == "out"
        assert (reaction.attributes or {})["dm.message.kind"] == "reaction"
        (thread,) = telemetry.named("instagram.event thread")
        assert "dm.direction" not in (thread.attributes or {})

    async def test_no_traceparent_while_tracing_is_off(
        self, tmp_path: Path, telemetry: Telemetry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(instruments, "tracer", trace.NoOpTracer())
        g = _gateway(tmp_path)
        (payload,) = await _feed(g, _message())
        assert "traceparent" not in payload
        assert telemetry.spans() == []

    async def test_a_closed_loop_ends_the_span(self, tmp_path: Path, telemetry: Telemetry) -> None:
        g = _gateway(tmp_path)
        g._loop = MagicMock()
        g._loop.call_soon_threadsafe.side_effect = RuntimeError("Event loop is closed")
        with pytest.raises(RuntimeError):
            g._on_event(_message())
        assert len(telemetry.named("instagram.event message")) == 1

    async def test_nothing_before_start(self, tmp_path: Path, telemetry: Telemetry) -> None:
        g = _gateway(tmp_path)
        g._loop = None
        g._on_event(_message())
        assert telemetry.spans() == []


class TestEventMetrics:
    async def test_messages_by_direction_and_kind(
        self, tmp_path: Path, telemetry: Telemetry, transcriber: Transcriber
    ) -> None:
        g = _gateway(tmp_path)
        g.client.download_message_media.side_effect = lambda _t, item, *_a: tmp_path / f"{item}.mp4"
        name = "dm.bridge.messages"
        labels = [
            {"direction": "in", "kind": "video"},
            {"direction": "out", "kind": "text"},
            {"direction": "in", "kind": "reaction"},
        ]
        before = [telemetry.total(name, platform="instagram", **x) for x in labels]
        published = telemetry.total(
            "dm.bridge.events.published", platform="instagram", type="message"
        )
        await _feed(
            g,
            _message("media", item_id="v1"),
            _message("text", user=int(ME), item_id="t1"),
            MessageEvent(THREAD, "t1", int(ME), "edited", "text", 1, edited=True),
            ReactionEvent(THREAD, "t1", HER, "emojis", "🔥"),
            TypingEvent(THREAD, HER, 1, 1),
        )
        after = [telemetry.total(name, platform="instagram", **x) for x in labels]
        assert [a - b for a, b in zip(after, before, strict=True)] == [1, 1, 1]
        published_after = telemetry.total(
            "dm.bridge.events.published", platform="instagram", type="message"
        )
        assert published_after - published == 3  # the edit goes out too, uncounted as new
        (video,) = [
            s
            for s in telemetry.named("instagram.event message")
            if (s.attributes or {}).get("messaging.message.id") == "v1"
        ]
        assert (video.attributes or {})["dm.message.kind"] == "video"

    async def test_media_duration(
        self, tmp_path: Path, telemetry: Telemetry, transcriber: Transcriber
    ) -> None:
        g = _gateway(tmp_path)
        labels = {"platform": "instagram", "kind": "audio", "outcome": "ok"}
        before = telemetry.total("dm.bridge.media.duration", **labels)
        await _feed(g, _message("voice_media"))
        assert telemetry.total("dm.bridge.media.duration", **labels) == before + 1
        metric = telemetry.metric("dm.bridge.media.duration")
        assert metric is not None and metric.unit == "s"
        assert tuple(metric.data.data_points[0].explicit_bounds) == SEND_BUCKETS  # type: ignore[union-attr]

    async def test_a_share_lookup_is_timed_as_a_share(
        self, tmp_path: Path, telemetry: Telemetry
    ) -> None:
        g = _gateway(tmp_path)
        g.client.share_details.side_effect = RuntimeError("gone")
        labels = {"platform": "instagram", "kind": "share", "outcome": "error"}
        before = telemetry.total("dm.bridge.media.duration", **labels)
        share = Share(kind="reel", media_id="1")
        await _feed(g, MessageEvent(THREAD, "s1", HER, None, "xma_clip", 1, share=share))
        assert telemetry.total("dm.bridge.media.duration", **labels) == before + 1
        assert len(telemetry.named("instagram.share.describe")) == 1

    async def test_a_slow_subscriber_drops_events(
        self, tmp_path: Path, telemetry: Telemetry
    ) -> None:
        g = _gateway(tmp_path)
        full: asyncio.Queue[str | None] = asyncio.Queue(maxsize=1)
        full.put_nowait("x")
        g._subscribers.add(full)
        labels = {"platform": "instagram", "reason": "slow_subscriber"}
        before = telemetry.total("dm.bridge.events.dropped", **labels)
        g._fan_out("data: {}\n\n")
        assert telemetry.total("dm.bridge.events.dropped", **labels) == before + 1

    async def test_sse_clients_and_queue_depths(self, tmp_path: Path, telemetry: Telemetry) -> None:
        g = _gateway(tmp_path)
        g.add_subscriber().put_nowait("a")
        g.add_subscriber().put_nowait("b")
        g.events.record("{}")
        instruments.sources.bridge_sse_clients = g.sse_clients
        instruments.sources.bridge_queues = g.queue_depths
        try:
            assert telemetry.total("dm.bridge.sse.clients", platform="instagram") == 2
            depth = "dm.bridge.queue.depth"
            assert telemetry.total(depth, queue="sse_backlog", platform="instagram") == 2
            assert telemetry.total(depth, queue="event_log", platform="instagram") == 1
            assert telemetry.total(depth, queue="media", platform="instagram") == 0
            g._media = None
            assert g.queue_depths()["media"] == 0
        finally:
            instruments.sources.bridge_sse_clients = None
            instruments.sources.bridge_queues = None
        assert telemetry.points("dm.bridge.queue.depth") == []


@pytest.mark.parametrize(
    ("item_type", "path", "share", "kind"),
    [
        ("text", None, False, "text"),
        ("link", None, False, "text"),
        ("media", None, False, "image"),
        ("media", "/m/a.MOV", False, "video"),
        ("raven_media", "/m/a.jpg", False, "image"),
        ("voice_media", "/m/a.m4a", False, "audio"),
        ("animated_media", None, False, "sticker"),
        ("like", None, False, "sticker"),
        ("clip", None, False, "share"),
        ("xma_story_share", None, False, "share"),
        ("text", None, True, "share"),
        ("location", None, False, "location"),
        ("action_log", None, False, "other"),
    ],
)
def test_message_kinds(item_type: str, path: str | None, share: bool, kind: str) -> None:
    assert message_kind(item_type, path, share=share) == kind
