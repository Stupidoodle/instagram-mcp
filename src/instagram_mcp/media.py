"""Inbound media: downloaded (and voice notes transcribed) before the event goes out.

A persona should never answer a photo or voice note it hasn't seen or heard, so the
event for inbound media is held until the file is on disk and, for voice, the
transcript is back. A shared reel or post, from either side, is held until its
caption and cover are in. Events are delivered in per-chat order; a slow download
only holds up its own chat. An event's span, when it has one, stays current until the
event went out, so the download and the transcription are its children.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
import time
from typing import TYPE_CHECKING

from opentelemetry import trace
from starlette.concurrency import run_in_threadpool

from instagram_mcp import instruments
from instagram_mcp.mqtt.events import Event, MessageEvent

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

    from opentelemetry.trace import Span

logger = logging.getLogger("instagram_mcp.media")

# Direct photos/videos, voice notes and disappearing photos (raven_media). Where a
# disappearing photo lands, kept or temporary, is the downloader's call by view mode.
MEDIA_ITEM_TYPES = {"media", "voice_media", "raven_media"}


def _ids(event: MessageEvent) -> dict[str, str]:
    """Span attributes of a fetch: the platform and the (random) message id."""
    return {"dm.platform": instruments.PLATFORM, "messaging.message.id": event.item_id}


def _failure(event: MessageEvent, exc: Exception) -> dict[str, str]:
    """Log fields for a failed fetch: the message id and the error class, no error text."""
    return {"message_id": event.item_id, "error_type": type(exc).__name__}


class InboundMedia:
    """Per-chat event queue that fetches inbound media before delivering its event."""

    def __init__(
        self,
        *,
        self_user_id: str,
        download: Callable[[MessageEvent], Path],
        transcribe: Callable[[Path], Awaitable[str]],
        describe_share: Callable[[MessageEvent], MessageEvent],
        deliver: Callable[[Event], None],
        timeout: float = 150.0,
    ) -> None:
        self.self_user_id = self_user_id
        self._download = download
        self._transcribe = transcribe
        self._describe_share = describe_share
        self._deliver = deliver
        self._timeout = timeout
        self._chains: dict[str, asyncio.Task[None]] = {}
        self._pending = 0

    @property
    def pending(self) -> int:
        """Events queued or being fetched, across every chat."""
        return self._pending

    def submit(self, event: Event, span: Span | None = None) -> None:
        """Queue an event behind the earlier ones of its chat (call on the event loop).

        ``span`` (the event's own) is ended once the event went out.
        """
        previous = self._chains.get(event.thread_id)
        self._pending += 1
        task = asyncio.get_running_loop().create_task(self._process(previous, event, span))
        self._chains[event.thread_id] = task
        task.add_done_callback(lambda done: self._forget(event.thread_id, done))

    def _forget(self, thread_id: str, done: asyncio.Task[None]) -> None:
        if self._chains.get(thread_id) is done:
            del self._chains[thread_id]

    async def drain(self) -> None:
        """Wait until every queued event went out."""
        while self._chains:
            await asyncio.gather(*self._chains.values(), return_exceptions=True)

    async def _process(
        self, previous: asyncio.Task[None] | None, event: Event, span: Span | None
    ) -> None:
        current = (
            trace.use_span(
                span, end_on_exit=True, record_exception=False, set_status_on_exception=False
            )
            if span is not None
            else contextlib.nullcontext()
        )
        try:
            with current:
                if previous is not None:
                    await asyncio.gather(previous, return_exceptions=True)
                if self._needs_media(event):
                    assert isinstance(event, MessageEvent)
                    event = await self._timed(self._enrich, event)
                elif (
                    isinstance(event, MessageEvent) and event.share is not None and not event.edited
                ):
                    event = await self._timed(self._share, event)
                self._deliver(event)
        finally:
            self._pending -= 1

    async def _timed(
        self, fetch: Callable[[MessageEvent], Awaitable[MessageEvent]], event: MessageEvent
    ) -> MessageEvent:
        """Run one fetch and record its duration, kind and outcome."""
        start = time.perf_counter()
        done = await fetch(event)
        attributes = {
            "platform": instruments.PLATFORM,
            "kind": instruments.message_kind(
                done.item_type, done.media_path, share=done.share is not None
            ),
            "outcome": "error" if done.media_error else "ok",
        }
        instruments.media_duration.record(time.perf_counter() - start, attributes)
        return done

    def _needs_media(self, event: Event) -> bool:
        return (
            isinstance(event, MessageEvent)
            and not event.edited
            and event.item_type in MEDIA_ITEM_TYPES
            and str(event.user_id) != self.self_user_id
        )

    async def _share(self, event: MessageEvent) -> MessageEvent:
        try:
            with instruments.span("instagram.share.describe", attributes=_ids(event)):
                return await asyncio.wait_for(
                    run_in_threadpool(self._describe_share, event), timeout=self._timeout
                )
        except Exception as exc:
            logger.warning("Share lookup failed", extra=_failure(event, exc))
            return dataclasses.replace(event, media_error=f"share lookup failed: {exc}")

    async def _enrich(self, event: MessageEvent) -> MessageEvent:
        try:
            with instruments.span("instagram.media.download", attributes=_ids(event)):
                path = await asyncio.wait_for(
                    run_in_threadpool(self._download, event), timeout=self._timeout
                )
        except Exception as exc:
            logger.warning("Media download failed", extra=_failure(event, exc))
            return dataclasses.replace(event, media_error=f"download failed: {exc}")
        if event.item_type != "voice_media":
            return dataclasses.replace(event, media_path=str(path))
        try:
            transcript = await asyncio.wait_for(self._transcribe(path), timeout=self._timeout)
        except Exception as exc:
            logger.warning("Transcription failed", extra=_failure(event, exc))
            return dataclasses.replace(
                event, media_path=str(path), media_error=f"transcription failed: {exc}"
            )
        return dataclasses.replace(event, media_path=str(path), transcript=transcript)
