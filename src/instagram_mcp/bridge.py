"""Instagram gateway daemon — the single owner of the Instagram connection.

One process holds the one MQTT (Iris) connection and the one instagrapi login,
and exposes the *domain* of Instagram DMs, not the plumbing:

- Commands over HTTP: send / send_media / send_voice / react / mark_read /
  typing / download, plus reads threads / thread / messages / search.
- Domain events over SSE (GET /events): message, reaction, read, typing,
  unsent, edited — broadcast to every subscriber; each client filters to the
  threads it cares about.

This mirrors the WhatsApp Go bridge: many Claude sessions share ONE upstream
connection instead of each opening its own (which Instagram's Iris starves down
to the most-recent socket). The per-session `instagram-mcp` is a thin client of
this daemon — it consumes /events and POSTs commands, and never touches MQTT.

Run: `uv run instagram-bridge` (defaults to 127.0.0.1:8082).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sys
import time
from typing import TYPE_CHECKING, Any

import httpx2
import uvicorn
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from instagram_mcp.client import (
    AuthenticationError,
    InstagramClient,
    InstagramClientError,
    SessionError,
)
from instagram_mcp.config import get_settings, setup_logging
from instagram_mcp.ephemeral import sweep
from instagram_mcp.media import InboundMedia
from instagram_mcp.mqtt.events import (
    MessageEvent,
    ReactionEvent,
    SeenEvent,
    ThreadEvent,
    TypingEvent,
    UnsendEvent,
)
from instagram_mcp.mqtt.manager import MQTTManager

if TYPE_CHECKING:
    import socket
    from collections.abc import AsyncIterator
    from pathlib import Path

    from starlette.requests import Request

    from instagram_mcp.mqtt.events import Event

logger = logging.getLogger("instagram_mcp.bridge")


def event_to_dict(event: Event) -> dict[str, Any] | None:  # noqa: PLR0911
    """Serialize a domain event for the SSE stream, or None to skip it."""
    if isinstance(event, MessageEvent):
        return {
            "type": "message",
            "thread_id": event.thread_id,
            "item_id": event.item_id,
            "user_id": str(event.user_id),
            "text": event.text,
            "item_type": event.item_type,
            "timestamp": event.timestamp,
            "edited": event.edited,
            "link_url": event.link_url,
            "link_title": event.link_title,
            "media_path": event.media_path,
            "transcript": event.transcript,
            "media_error": event.media_error,
            "view_mode": event.view_mode,
        }
    if isinstance(event, ReactionEvent):
        return {
            "type": "reaction",
            "thread_id": event.thread_id,
            "item_id": event.item_id,
            "user_id": str(event.user_id),
            "reaction_type": event.reaction_type,
            "emoji": event.emoji,
        }
    if isinstance(event, SeenEvent):
        return {
            "type": "read",
            "thread_id": event.thread_id,
            "user_id": str(event.user_id),
            "item_id": event.item_id,
            "timestamp": event.timestamp,
        }
    if isinstance(event, TypingEvent):
        return {
            "type": "typing",
            "thread_id": event.thread_id,
            "user_id": str(event.user_id),
            "activity_status": event.activity_status,
            "ttl": event.ttl,
        }
    if isinstance(event, UnsendEvent):
        return {
            "type": "unsent",
            "thread_id": event.thread_id,
            "item_id": event.item_id,
            "user_id": str(event.user_id),
        }
    if isinstance(event, ThreadEvent):
        return {"type": "thread", "thread_id": event.thread_id, "op": event.op, "path": event.path}
    return None


class Gateway:
    """Holds the connection and fans domain events out to SSE subscribers."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.client = InstagramClient(
            session_file=self.settings.instagram_session_file,
            app_version=self.settings.instagram_app_version,
        )
        self.mqtt: MQTTManager | None = None
        self.self_user_id = ""
        self._subscribers: set[asyncio.Queue[str | None]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._media: InboundMedia | None = None
        self._sweeper: asyncio.Task[None] | None = None

    # ── lifecycle ──────────────────────────────────────────────────────────

    def start(self) -> None:
        """Log in and open the single MQTT connection (called on app startup)."""
        self._loop = asyncio.get_running_loop()
        self.client.login_or_load_session(
            username=self.settings.instagram_username,
            password=self.settings.instagram_password.get_secret_value(),
        )
        self.self_user_id = str(self.client.client.user_id or "")
        self._media = InboundMedia(
            self_user_id=self.self_user_id,
            download=self._download_media,
            transcribe=self._transcribe,
            deliver=self._deliver,
        )
        self._sweeper = self._loop.create_task(self._sweep_ephemeral())
        iris = self.client.get_iris_info()
        self.mqtt = MQTTManager()
        self.mqtt.set_listener(self._on_event)
        try:
            self.mqtt.connect(
                session_file=self.settings.instagram_session_file,
                seq_id=iris["seq_id"],
                snapshot_at_ms=iris["snapshot_at_ms"],
                app_version=iris["app_version"],
            )
            logger.info("Instagram gateway connected (seq_id=%d)", iris["seq_id"])
        except Exception:
            logger.warning("MQTT connect failed; watchdog will keep retrying", exc_info=True)
        self.mqtt.start_watchdog()

    def stop(self) -> None:
        """Disconnect the MQTT connection (called on app shutdown)."""
        if self._sweeper is not None:
            self._sweeper.cancel()
        if self.mqtt is not None:
            self.mqtt.disconnect()

    # ── event fan-out (called on the MQTT reader thread) ───────────────────

    def _on_event(self, event: Event) -> None:
        if self._loop is None or self._media is None:
            return
        # Hop from the reader thread onto the event loop; InboundMedia keeps chat order.
        self._loop.call_soon_threadsafe(self._media.submit, event)

    def _deliver(self, event: Event) -> None:
        payload = event_to_dict(event)
        if payload is not None:
            self._fan_out(f"data: {json.dumps(payload)}\n\n")

    def _download_media(self, event: MessageEvent) -> Path:
        """Download an inbound message's media (on a worker thread)."""
        folder = self.settings.instagram_media_dir.resolve() / event.thread_id
        ephemeral = self.settings.instagram_ephemeral_dir.resolve()
        try:
            return self.client.download_message_media(
                event.thread_id, event.item_id, folder, ephemeral
            )
        except InstagramClientError:
            # The push can beat the REST API by a moment; try once more.
            time.sleep(2)
            return self.client.download_message_media(
                event.thread_id, event.item_id, folder, ephemeral
            )

    async def _sweep_ephemeral(self) -> None:
        """Delete view-once and replayable downloads once their time is up."""
        ttl = self.settings.instagram_ephemeral_ttl_minutes * 60
        while True:
            try:
                removed = await asyncio.to_thread(sweep, self.settings.instagram_ephemeral_dir, ttl)
            except OSError:
                logger.warning("Sweeping view-once downloads failed", exc_info=True)
            else:
                if removed:
                    logger.info("Deleted %d expired view-once download(s)", removed)
            await asyncio.sleep(60)

    async def _transcribe(self, path: Path) -> str:
        url = self.settings.instagram_transcriber_url.rstrip("/") + "/transcribe"
        async with httpx2.AsyncClient(timeout=150) as http:
            resp = await http.post(url, json={"path": str(path)})
        if resp.status_code != 200:
            raise RuntimeError(resp.json().get("error", f"HTTP {resp.status_code}"))
        return str(resp.json()["text"])

    def _fan_out(self, line: str) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(line)
            except asyncio.QueueFull:
                logger.warning("Dropping event for a slow SSE subscriber")

    def add_subscriber(self) -> asyncio.Queue[str | None]:
        """Register a new SSE subscriber queue."""
        q: asyncio.Queue[str | None] = asyncio.Queue(maxsize=1000)
        self._subscribers.add(q)
        return q

    def remove_subscriber(self, q: asyncio.Queue[str | None]) -> None:
        """Drop an SSE subscriber queue."""
        self._subscribers.discard(q)

    def close_streams(self) -> None:
        """End every SSE stream (None is the end marker); clients reconnect."""
        for q in list(self._subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(None)


gateway: Gateway | None = None


def gw() -> Gateway:
    """Return the running gateway singleton."""
    if gateway is None:  # pragma: no cover - set at startup
        msg = "gateway not initialized"
        raise RuntimeError(msg)
    return gateway


# ── HTTP: domain event stream ──────────────────────────────────────────────


async def event_stream(g: Gateway, q: asyncio.Queue[str | None]) -> AsyncIterator[str]:
    """One subscriber's SSE lines, until the gateway closes the stream."""
    yield ": connected\n\n"
    try:
        while True:
            try:
                line = await asyncio.wait_for(q.get(), timeout=20)
            except TimeoutError:
                yield ": keepalive\n\n"  # keeps proxies/clients from timing out
                continue
            if line is None:
                return
            yield line
    finally:
        g.remove_subscriber(q)


async def sse_events(_request: Request) -> StreamingResponse:
    """SSE stream of every domain event; clients filter to their threads."""
    g = gw()
    return StreamingResponse(event_stream(g, g.add_subscriber()), media_type="text/event-stream")


# ── HTTP: commands + reads (each delegates to the one instagrapi login) ─────


async def _json(request: Request) -> dict[str, Any]:
    """Parse the request JSON body, or {} on error."""
    try:
        return await request.json()
    except Exception:
        return {}


def _err(message: str, code: int = 400) -> JSONResponse:
    """A {success: false, error} response with the given status code."""
    return JSONResponse({"success": False, "error": message}, status_code=code)


async def health(_request: Request) -> JSONResponse:
    """Liveness plus the logged-in user id and MQTT status."""
    g = gw()
    connected = g.mqtt is not None and g.mqtt.is_connected
    return JSONResponse({"ok": True, "self_user_id": g.self_user_id, "mqtt_connected": connected})


async def send(request: Request) -> JSONResponse:
    """Send a text message to a thread."""
    body = await _json(request)
    thread_id, text = body.get("thread_id"), body.get("text", "")
    if not thread_id or not text:
        return _err("thread_id and text required")
    msg = await run_in_threadpool(gw().client.reply_to_thread, thread_id=thread_id, text=text)
    if msg is None:
        return _err("not confirmed", 502)
    return JSONResponse({"success": True, "message_id": msg.message_id})


async def send_media(request: Request) -> JSONResponse:
    """Send a photo or video to a thread."""
    from pathlib import Path

    body = await _json(request)
    thread_id, path, kind = body.get("thread_id"), body.get("path"), body.get("kind", "photo")
    if not thread_id or not path:
        return _err("thread_id and path required")
    fn = gw().client.send_video if kind == "video" else gw().client.send_photo
    msg = await run_in_threadpool(fn, path=Path(path), thread_ids=[thread_id])
    if msg is None:
        return _err("not confirmed", 502)
    return JSONResponse({"success": True, "message_id": msg.message_id})


async def send_voice(request: Request) -> JSONResponse:
    """Send a voice message to a thread."""
    from pathlib import Path

    body = await _json(request)
    thread_id, path = body.get("thread_id"), body.get("path")
    if not thread_id or not path:
        return _err("thread_id and path required")
    msg = await run_in_threadpool(gw().client.send_voice, Path(path), thread_id)
    if msg is None:
        return _err("not confirmed", 502)
    return JSONResponse({"success": True, "message_id": msg.message_id})


async def react(request: Request) -> JSONResponse:
    """Add or remove a reaction on a message."""
    body = await _json(request)
    thread_id, message_id = body.get("thread_id"), body.get("message_id")
    emoji, remove = body.get("emoji", ""), bool(body.get("remove", False))
    if not thread_id or not message_id:
        return _err("thread_id and message_id required")
    ok = await run_in_threadpool(gw().client.react, thread_id, message_id, emoji, remove=remove)
    return JSONResponse({"success": bool(ok)})


async def mark_read(request: Request) -> JSONResponse:
    """Send a read receipt up to a message."""
    body = await _json(request)
    thread_id, message_id = body.get("thread_id"), body.get("message_id")
    if not thread_id or not message_id:
        return _err("thread_id and message_id required")
    ok = await run_in_threadpool(gw().client.mark_seen, thread_id, message_id)
    return JSONResponse({"success": bool(ok)})


async def typing(request: Request) -> JSONResponse:
    """Show or clear the typing indicator in a thread."""
    body = await _json(request)
    thread_id, active = body.get("thread_id"), bool(body.get("active", True))
    g = gw()
    if not thread_id:
        return _err("thread_id required")
    if g.mqtt is None:
        return _err("no mqtt connection", 503)
    try:
        await run_in_threadpool(g.mqtt.indicate_activity, thread_id, active=active)
    except Exception as e:
        return _err(str(e), 502)
    return JSONResponse({"success": True})


async def unsend(request: Request) -> JSONResponse:
    """Unsend (delete for everyone) one of our own messages."""
    body = await _json(request)
    thread_id, message_id = body.get("thread_id"), body.get("message_id")
    if not thread_id or not message_id:
        return _err("thread_id and message_id required")
    ok = await run_in_threadpool(gw().client.delete_message, thread_id, message_id)
    return JSONResponse({"success": bool(ok)})


async def download(request: Request) -> JSONResponse:
    """Download a message's media and return the local path."""
    body = await _json(request)
    thread_id, message_id = body.get("thread_id"), body.get("message_id")
    if not thread_id or not message_id:
        return _err("thread_id and message_id required")
    g = gw()
    try:
        path = await run_in_threadpool(
            g.client.download_message_media,
            thread_id,
            message_id,
            g.settings.instagram_media_dir,
            g.settings.instagram_ephemeral_dir,
        )
    except Exception as e:
        return _err(str(e), 502)
    return JSONResponse({"success": True, "path": str(path.resolve())})


def _thread_json(thread: Any) -> dict[str, Any]:
    """Serialize a thread to the bridge's JSON shape."""
    users = [
        {"user_id": u.user_id, "username": u.username, "full_name": u.full_name}
        for u in thread.users
    ]
    last = getattr(thread, "last_activity_at", None)
    return {
        "thread_id": thread.thread_id,
        "thread_title": thread.thread_title,
        "users": users,
        "is_group": thread.is_group,
        "is_muted": getattr(thread, "is_muted", False),
        "unread": getattr(thread, "unread", False),
        "last_activity_at": last.isoformat() if last else None,
    }


async def threads(request: Request) -> JSONResponse:
    """List recent DM threads."""
    amount = int(request.query_params.get("amount", "20"))
    result = await run_in_threadpool(gw().client.get_threads, amount)
    return JSONResponse({"threads": [_thread_json(t) for t in result]})


async def search(request: Request) -> JSONResponse:
    """Search DM threads by query."""
    query = request.query_params.get("query", "")
    result = await run_in_threadpool(gw().client.search_threads, query)
    return JSONResponse({"threads": [_thread_json(t) for t in result]})


def _msg_json(m: Any) -> dict[str, Any]:
    """Serialize a message to the bridge's JSON shape."""
    return {
        "message_id": m.message_id,
        "user_id": m.sender.user_id,
        "username": m.sender.username,
        "text": m.content.text,
        "media_type": m.content.media_type.value,
        "media_url": m.content.media_url,
        "link_url": m.content.link_url,
        "link_title": m.content.link_title,
        "timestamp": m.timestamp.isoformat(),
        "is_from_me": m.is_sent_by_viewer,
        "seen_since": m.seen_since,
        "reactions": [{"user_id": r.user_id, "emoji": r.emoji} for r in m.reactions],
    }


async def messages(request: Request) -> JSONResponse:
    """Get recent messages in a thread."""
    thread_id = request.query_params.get("thread_id", "")
    amount = int(request.query_params.get("amount", "20"))
    if not thread_id:
        return JSONResponse({"error": "thread_id required"}, status_code=400)
    result = await run_in_threadpool(gw().client.get_messages, thread_id, amount)
    return JSONResponse({"thread_id": thread_id, "messages": [_msg_json(m) for m in result]})


async def thread(request: Request) -> JSONResponse:
    """Get one thread with its recent messages."""
    thread_id = request.query_params.get("thread_id", "")
    amount = int(request.query_params.get("amount", "20"))
    if not thread_id:
        return JSONResponse({"error": "thread_id required"}, status_code=400)
    t = await run_in_threadpool(gw().client.get_thread, thread_id, amount)
    data = _thread_json(t)
    data["messages"] = [_msg_json(m) for m in (t.messages or [])]
    return JSONResponse(data)


async def pending(_request: Request) -> JSONResponse:
    """List pending (message-request) threads."""
    result = await run_in_threadpool(gw().client.get_pending_threads)
    return JSONResponse({"threads": [_thread_json(t) for t in result]})


async def hide(request: Request) -> JSONResponse:
    """Hide/delete a thread from the inbox."""
    body = await _json(request)
    if not body.get("thread_id"):
        return _err("thread_id required")
    ok = await run_in_threadpool(gw().client.hide_thread, body["thread_id"])
    return JSONResponse({"success": bool(ok)})


async def mark_unread(request: Request) -> JSONResponse:
    """Mark a thread unread."""
    body = await _json(request)
    if not body.get("thread_id"):
        return _err("thread_id required")
    ok = await run_in_threadpool(gw().client.mark_thread_unread, body["thread_id"])
    return JSONResponse({"success": bool(ok)})


async def mute(request: Request) -> JSONResponse:
    """Mute a thread."""
    body = await _json(request)
    if not body.get("thread_id"):
        return _err("thread_id required")
    ok = await run_in_threadpool(gw().client.mute_thread, body["thread_id"])
    return JSONResponse({"success": bool(ok)})


async def unmute(request: Request) -> JSONResponse:
    """Unmute a thread."""
    body = await _json(request)
    if not body.get("thread_id"):
        return _err("thread_id required")
    ok = await run_in_threadpool(gw().client.unmute_thread, body["thread_id"])
    return JSONResponse({"success": bool(ok)})


async def share_media(request: Request) -> JSONResponse:
    """Share a media post into a thread."""
    body = await _json(request)
    media_id, thread_id = body.get("media_id"), body.get("thread_id")
    if not media_id or not thread_id:
        return _err("media_id and thread_id required")
    ok = await run_in_threadpool(gw().client.share_media, media_id, None, [thread_id])
    return JSONResponse({"success": bool(ok)})


async def share_profile(request: Request) -> JSONResponse:
    """Share a user profile into a thread."""
    body = await _json(request)
    user_id, thread_id = body.get("user_id"), body.get("thread_id")
    if not user_id or not thread_id:
        return _err("user_id and thread_id required")
    ok = await run_in_threadpool(gw().client.share_profile, user_id, None, [thread_id])
    return JSONResponse({"success": bool(ok)})


def build_app() -> Starlette:
    """Build the Starlette app: routes + connection lifespan."""
    routes = [
        Route("/events", sse_events),
        Route("/health", health),
        Route("/send", send, methods=["POST"]),
        Route("/send_media", send_media, methods=["POST"]),
        Route("/send_voice", send_voice, methods=["POST"]),
        Route("/react", react, methods=["POST"]),
        Route("/mark_read", mark_read, methods=["POST"]),
        Route("/typing", typing, methods=["POST"]),
        Route("/download", download, methods=["POST"]),
        Route("/unsend", unsend, methods=["POST"]),
        Route("/threads", threads),
        Route("/thread", thread),
        Route("/thread_search", search),
        Route("/messages", messages),
        Route("/pending", pending),
        Route("/hide", hide, methods=["POST"]),
        Route("/mark_unread", mark_unread, methods=["POST"]),
        Route("/mute", mute, methods=["POST"]),
        Route("/unmute", unmute, methods=["POST"]),
        Route("/share_media", share_media, methods=["POST"]),
        Route("/share_profile", share_profile, methods=["POST"]),
    ]

    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        global gateway  # noqa: PLW0603
        gateway = Gateway()
        gateway.start()
        try:
            yield
        finally:
            gateway.stop()

    return Starlette(routes=routes, lifespan=lifespan)


class BridgeServer(uvicorn.Server):
    """Ends the SSE streams before uvicorn waits for connections to close.

    Without this, the never-ending streams hold every shutdown until systemd
    kills the process, and the MQTT connection is never closed.
    """

    async def shutdown(self, sockets: list[socket.socket] | None = None) -> None:
        """Close the streams, then shut down as usual."""
        if gateway is not None:
            gateway.close_streams()
        await super().shutdown(sockets)


def main() -> None:
    """Entry point for `uv run instagram-bridge`."""
    settings = get_settings()
    setup_logging(settings.log_level)
    host = settings.instagram_bridge_host
    port = settings.instagram_bridge_port
    logger.info("Starting Instagram bridge on %s:%d", host, port)
    config = uvicorn.Config(
        build_app(),
        host=host,
        port=port,
        log_level=settings.log_level.lower(),
        timeout_graceful_shutdown=5,  # backstop for a client that won't let go
    )
    server = BridgeServer(config)
    try:
        server.run()
    except (AuthenticationError, SessionError) as e:
        logger.error("Instagram auth failed: %s. Run instagram-mcp-login first.", e)
        raise
    if not server.started:
        sys.exit(3)  # uvicorn's startup-failure code, so systemd restarts us


if __name__ == "__main__":
    main()
