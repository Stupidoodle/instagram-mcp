"""No message text, names, user ids or thread ids in INFO+ log lines, even from errors."""

from __future__ import annotations

import io
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from instagram_mcp import logs, server
from instagram_mcp.bridge_client import event_from_dict
from instagram_mcp.catch_up import CatchUp, ChannelState
from instagram_mcp.channel import Channel
from instagram_mcp.media import InboundMedia
from instagram_mcp.mqtt.events import MessageEvent

if TYPE_CHECKING:
    from instagram_mcp.config import Settings
    from instagram_mcp.mqtt.events import Event

pytestmark = pytest.mark.usefixtures("restore_logging")

THREAD = "340282366841700000000000000000000000077"
USER = 5550001234
NAME = "Alexandra Muller"
TEXT = "meet me at the old bridge at nine"
PRIVATE = (THREAD, str(USER), NAME, TEXT, "alexandra.m")


def _message(item_type: str = "text") -> MessageEvent:
    return MessageEvent(THREAD, "i1", USER, TEXT, item_type, 1_770_000_000_000_000)


def _leak(detail: str) -> Exception:
    return RuntimeError(f"GET /direct_v2/threads/{THREAD}/ for {NAME}: {TEXT} ({detail})")


@pytest.fixture
def lines() -> io.StringIO:
    stream = io.StringIO()
    logs.setup("INFO", service="instagram-bridge", stream=stream)
    return stream


def _assert_clean(stream: io.StringIO, expected: int) -> list[dict[str, Any]]:
    output = stream.getvalue()
    parsed = [json.loads(line) for line in output.splitlines()]
    assert len(parsed) >= expected
    for secret in PRIVATE:
        assert secret not in output
    return parsed


async def test_a_voice_note_whose_download_fails(lines: io.StringIO) -> None:
    def download(_event: MessageEvent) -> Path:
        raise _leak("download")

    delivered: list[Event] = []
    media = InboundMedia(
        self_user_id="1",
        download=download,
        transcribe=AsyncMock(),
        describe_share=MagicMock(),
        deliver=delivered.append,
    )
    media.submit(_message("voice_media"))
    await media.drain()
    (line,) = _assert_clean(lines, 1)
    assert line["msg"] == "Media download failed"
    assert (line["message_id"], line["error_type"]) == ("i1", "RuntimeError")


async def test_a_voice_note_whose_transcription_fails(lines: io.StringIO) -> None:
    media = InboundMedia(
        self_user_id="1",
        download=lambda _e: Path("/media/a.m4a"),
        transcribe=AsyncMock(side_effect=_leak("transcribe")),
        describe_share=MagicMock(side_effect=_leak("share")),
        deliver=lambda _e: None,
    )
    media.submit(_message("voice_media"))
    share = MessageEvent(THREAD, "i2", USER, TEXT, "xma_clip", 1, share=MagicMock())
    media.submit(share)
    await media.drain()
    assert [x["msg"] for x in _assert_clean(lines, 2)] == [
        "Transcription failed",
        "Share lookup failed",
    ]


async def test_a_message_through_the_channel(lines: io.StringIO) -> None:
    channel = Channel(self_user_id="1", describe_thread=lambda _t: (NAME, {str(USER): NAME}))
    channel.subscribe(THREAD, "alex")
    session = MagicMock()
    session.send_notification = AsyncMock(side_effect=_leak("push"))
    channel.attach(session)
    channel.handle(_message())
    await channel._send(TEXT, {"chat": "alex"})
    assert channel._idle_task is not None
    channel._idle_task.cancel()
    parsed = _assert_clean(lines, 3)
    assert [x["msg"] for x in parsed][:2] == ["Subscribed a chat", "Channel attached"]
    assert parsed[0]["alias"] == "alex"
    assert parsed[-1]["msg"] == "Could not push to Claude Code"
    assert parsed[-1]["error_type"] == "RuntimeError"


def test_a_failed_catch_up(lines: io.StringIO, tmp_path: Path) -> None:
    def history(_thread: str, _amount: int) -> list[dict[str, Any]]:
        raise _leak("history")

    catch_up = CatchUp(ChannelState(tmp_path / "s.json"), history, lambda: [THREAD], MagicMock())
    catch_up.fill_gap()
    (line,) = _assert_clean(lines, 1)
    assert line["msg"] == "Could not catch up on a chat"


def test_a_malformed_bridge_event(lines: io.StringIO) -> None:
    assert event_from_dict({"type": "message", "text": TEXT, "user_id": USER}) is None
    (line,) = _assert_clean(lines, 1)
    assert (line["msg"], line["event"]) == ("Malformed bridge event", "message")


def test_a_rejected_subscription(lines: io.StringIO, mock_settings: Settings) -> None:
    settings = mock_settings.model_copy(
        update={"instagram_subscribe": f"alex={THREAD},alex={THREAD[:-1]}8"}
    )
    bridge = MagicMock()
    bridge.self_user_id.return_value = "1"
    bridge.thread.return_value = {"thread_title": NAME, "users": []}
    with (
        patch("instagram_mcp.server.setup_logging", return_value=logging.getLogger(logs.PACKAGE)),
        patch("instagram_mcp.server.BridgeClient", return_value=bridge),
        patch("instagram_mcp.server.threading.Thread"),
    ):
        server.create_server(settings)
    parsed = _assert_clean(lines, 2)
    skipped = [x for x in parsed if x["msg"] == "Skipping an INSTAGRAM_SUBSCRIBE entry"]
    assert skipped == [skipped[0] | {"alias": "alex", "error_type": "ChannelError"}]


def test_instagram_retries(instagram_client: Any, lines: io.StringIO) -> None:
    calls: list[int] = []

    def flaky() -> str:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError(f"429 Too Many Requests for /direct_v2/threads/{THREAD}/")
        return "ok"

    with patch("instagram_mcp.client.time.sleep"):
        assert instagram_client._retry_on_rate_limit(flaky) == "ok"
    (line,) = _assert_clean(lines, 1)
    assert (line["msg"], line["attempt"]) == ("Rate limited or timed out; retrying", 1)
