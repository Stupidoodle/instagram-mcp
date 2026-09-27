"""Inbound media: downloaded (and voice transcribed) before its event goes out, in chat order."""

from __future__ import annotations

import asyncio
from pathlib import Path

from instagram_mcp.media import InboundMedia
from instagram_mcp.mqtt.events import Event, MessageEvent

ME = "1000"


def _msg(item_type: str, item_id: str = "i1", thread: str = "t1", user: int = 42) -> MessageEvent:
    return MessageEvent(thread, item_id, user, None, item_type, 1)


class Harness:
    def __init__(
        self,
        *,
        download_error: Exception | None = None,
        transcribe_error: Exception | None = None,
        download_delay: float = 0.0,
    ) -> None:
        self.delivered: list[Event] = []
        self.downloads: list[str] = []
        self.transcribed: list[Path] = []
        self._download_error = download_error
        self._transcribe_error = transcribe_error
        self._download_delay = download_delay
        self.media = InboundMedia(
            self_user_id=ME,
            download=self._download,
            transcribe=self._transcribe,
            deliver=self.delivered.append,
        )

    def _download(self, event: MessageEvent) -> Path:
        self.downloads.append(event.item_id)
        if self._download_delay:
            import time

            time.sleep(self._download_delay)
        if self._download_error is not None:
            raise self._download_error
        suffix = ".m4a" if event.item_type == "voice_media" else ".jpg"
        return Path(f"/media/{event.thread_id}/{event.item_id}{suffix}")

    async def _transcribe(self, path: Path) -> str:
        self.transcribed.append(path)
        if self._transcribe_error is not None:
            raise self._transcribe_error
        return "hallo zäme"


async def _run(h: Harness, *events: Event) -> None:
    for event in events:
        h.media.submit(event)
    await h.media.drain()


async def test_voice_note_arrives_with_path_and_transcript() -> None:
    h = Harness()
    await _run(h, _msg("voice_media"))
    (event,) = h.delivered
    assert isinstance(event, MessageEvent)
    assert event.media_path == "/media/t1/i1.m4a"
    assert event.transcript == "hallo zäme"
    assert event.media_error is None


async def test_photo_arrives_with_path_and_is_not_transcribed() -> None:
    h = Harness()
    await _run(h, _msg("media"))
    (event,) = h.delivered
    assert isinstance(event, MessageEvent)
    assert event.media_path == "/media/t1/i1.jpg"
    assert h.transcribed == []


async def test_own_media_passes_through_untouched() -> None:
    h = Harness()
    own = _msg("voice_media", user=int(ME))
    await _run(h, own)
    assert h.delivered == [own]
    assert h.downloads == []


async def test_disappearing_photos_are_fetched_but_never_transcribed() -> None:
    # Where they land (temporary or kept) is the downloader's call, by view mode.
    h = Harness()
    await _run(h, _msg("raven_media", item_id="once"), _msg("raven_media", item_id="kept"))
    assert h.downloads == ["once", "kept"]
    assert h.transcribed == []
    assert all(isinstance(e, MessageEvent) and e.media_path for e in h.delivered)


async def test_a_failed_download_still_delivers_with_an_error() -> None:
    h = Harness(download_error=RuntimeError("not in the latest 50"))
    await _run(h, _msg("media"))
    (event,) = h.delivered
    assert isinstance(event, MessageEvent)
    assert event.media_path is None
    assert event.media_error is not None
    assert "not in the latest 50" in event.media_error


async def test_a_failed_transcription_keeps_the_file() -> None:
    h = Harness(transcribe_error=RuntimeError("all engines failed"))
    await _run(h, _msg("voice_media"))
    (event,) = h.delivered
    assert isinstance(event, MessageEvent)
    assert event.media_path == "/media/t1/i1.m4a"
    assert event.transcript is None
    assert event.media_error is not None


async def test_chat_order_holds_and_other_chats_are_not_blocked() -> None:
    h = Harness(download_delay=0.2)
    slow_voice = _msg("voice_media", item_id="a", thread="t1")
    later_text = _msg("text", item_id="b", thread="t1")
    other_chat = _msg("text", item_id="c", thread="t2")
    for event in (slow_voice, later_text, other_chat):
        h.media.submit(event)
    await asyncio.sleep(0.05)
    # The other chat didn't wait for t1's download; t1's text did.
    assert [e.item_id for e in h.delivered if isinstance(e, MessageEvent)] == ["c"]
    await h.media.drain()
    assert [e.item_id for e in h.delivered if isinstance(e, MessageEvent)] == ["c", "a", "b"]
