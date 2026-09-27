"""Client side of the Instagram gateway — how the thin MCP talks to the bridge.

The per-session MCP no longer opens its own Instagram connection. It:
- issues domain commands / reads to the bridge over HTTP (`BridgeClient`), and
- consumes the bridge's domain-event SSE stream (`stream_events`), rebuilding the
  same `Event` objects the channel already understands.

One bridge, many thin clients — the WhatsApp pub/sub shape.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

import httpx2

from instagram_mcp.mqtt.events import (
    Event,
    MessageEvent,
    ReactionEvent,
    SeenEvent,
    ThreadEvent,
    TypingEvent,
    UnsendEvent,
)

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger("instagram_mcp.bridge_client")


class BridgeError(RuntimeError):
    """The bridge was unreachable or returned an error."""


def event_from_dict(d: dict[str, Any]) -> Event | None:  # noqa: PLR0911
    """Rebuild an Event from the bridge's SSE JSON (inverse of bridge.event_to_dict)."""
    kind = d.get("type")
    try:
        if kind == "message":
            return MessageEvent(
                thread_id=d["thread_id"],
                item_id=d["item_id"],
                user_id=int(d.get("user_id") or 0),
                text=d.get("text"),
                item_type=d.get("item_type", "unknown"),
                timestamp=int(d.get("timestamp") or 0),
                edited=bool(d.get("edited", False)),
            )
        if kind == "reaction":
            return ReactionEvent(
                thread_id=d["thread_id"],
                item_id=d["item_id"],
                user_id=int(d.get("user_id") or 0),
                reaction_type=d.get("reaction_type", "emojis"),
                emoji=d.get("emoji"),
            )
        if kind == "read":
            return SeenEvent(
                thread_id=d["thread_id"],
                user_id=int(d.get("user_id") or 0),
                item_id=d.get("item_id", ""),
                timestamp=int(d.get("timestamp") or 0),
            )
        if kind == "typing":
            return TypingEvent(
                thread_id=d["thread_id"],
                user_id=int(d.get("user_id") or 0),
                activity_status=int(d.get("activity_status") or 0),
                ttl=int(d.get("ttl") or 0),
            )
        if kind == "unsent":
            return UnsendEvent(
                thread_id=d["thread_id"],
                item_id=d["item_id"],
                user_id=int(d.get("user_id") or 0),
            )
        if kind == "thread":
            return ThreadEvent(thread_id=d["thread_id"], op=d.get("op", ""), path=d.get("path", ""))
    except KeyError, ValueError, TypeError:
        logger.warning("Malformed bridge event: %s", d)
    return None


class BridgeClient:
    """Synchronous HTTP client for the bridge's command + read endpoints.

    The MCP tool functions are sync (run in a worker thread by MCPServer), so a
    sync httpx client is the natural fit.
    """

    def __init__(self, base_url: str, timeout: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = httpx2.Client(base_url=self.base_url, timeout=timeout)

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._http.close()

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = self._http.post(path, json=body)
        except httpx2.HTTPError as e:
            raise BridgeError(f"bridge unreachable: {e}") from e
        data = resp.json()
        if resp.status_code >= 400 and "error" not in data:
            data = {"success": False, "error": f"HTTP {resp.status_code}"}
        return data

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            resp = self._http.get(path, params=params)
        except httpx2.HTTPError as e:
            raise BridgeError(f"bridge unreachable: {e}") from e
        return resp.json()

    # ── identity ───────────────────────────────────────────────────────────
    def health(self) -> dict[str, Any]:
        """Bridge health, including the logged-in `self_user_id`."""
        return self._get("/health")

    def self_user_id(self) -> str:
        """The logged-in account's user id (empty string if unknown)."""
        try:
            return str(self.health().get("self_user_id", ""))
        except BridgeError:
            return ""

    # ── commands ───────────────────────────────────────────────────────────
    def send(self, thread_id: str, text: str) -> dict[str, Any]:
        """Send a text message."""
        return self._post("/send", {"thread_id": thread_id, "text": text})

    def send_media(self, thread_id: str, path: str, kind: str) -> dict[str, Any]:
        """Send a photo or video (`kind` = 'photo' | 'video')."""
        return self._post("/send_media", {"thread_id": thread_id, "path": path, "kind": kind})

    def send_voice(self, thread_id: str, path: str) -> dict[str, Any]:
        """Send a voice message."""
        return self._post("/send_voice", {"thread_id": thread_id, "path": path})

    def react(
        self, thread_id: str, message_id: str, emoji: str, *, remove: bool = False
    ) -> dict[str, Any]:
        """Add or remove a reaction."""
        return self._post(
            "/react",
            {"thread_id": thread_id, "message_id": message_id, "emoji": emoji, "remove": remove},
        )

    def mark_read(self, thread_id: str, message_id: str) -> dict[str, Any]:
        """Send a read receipt up to a message."""
        return self._post("/mark_read", {"thread_id": thread_id, "message_id": message_id})

    def typing(self, thread_id: str, *, active: bool = True) -> dict[str, Any]:
        """Show or clear the typing indicator."""
        return self._post("/typing", {"thread_id": thread_id, "active": active})

    def download(self, thread_id: str, message_id: str) -> dict[str, Any]:
        """Download a message's media; returns `{success, path}`."""
        return self._post("/download", {"thread_id": thread_id, "message_id": message_id})

    def unsend(self, thread_id: str, message_id: str) -> dict[str, Any]:
        """Unsend (delete for everyone) one of our own messages."""
        return self._post("/unsend", {"thread_id": thread_id, "message_id": message_id})

    def hide(self, thread_id: str) -> dict[str, Any]:
        """Hide/delete a thread."""
        return self._post("/hide", {"thread_id": thread_id})

    def mark_unread(self, thread_id: str) -> dict[str, Any]:
        """Mark a thread unread."""
        return self._post("/mark_unread", {"thread_id": thread_id})

    def mute(self, thread_id: str) -> dict[str, Any]:
        """Mute a thread."""
        return self._post("/mute", {"thread_id": thread_id})

    def unmute(self, thread_id: str) -> dict[str, Any]:
        """Unmute a thread."""
        return self._post("/unmute", {"thread_id": thread_id})

    def share_media(self, media_id: str, thread_id: str) -> dict[str, Any]:
        """Share a media post into a thread."""
        return self._post("/share_media", {"media_id": media_id, "thread_id": thread_id})

    def share_profile(self, user_id: str, thread_id: str) -> dict[str, Any]:
        """Share a user profile into a thread."""
        return self._post("/share_profile", {"user_id": user_id, "thread_id": thread_id})

    # ── reads ──────────────────────────────────────────────────────────────
    def threads(self, amount: int = 20) -> list[dict[str, Any]]:
        """List recent threads."""
        resp = self._get("/threads", {"amount": amount})
        return resp.get("threads", [])

    def thread(self, thread_id: str, amount: int = 20) -> dict[str, Any]:
        """One thread with its recent messages."""
        return self._get("/thread", {"thread_id": thread_id, "amount": amount})

    def search(self, query: str) -> list[dict[str, Any]]:
        """Search threads."""
        resp = self._get("/thread_search", {"query": query})
        return resp.get("threads", [])

    def messages(self, thread_id: str, amount: int = 20) -> list[dict[str, Any]]:
        """Recent messages in a thread."""
        resp = self._get("/messages", {"thread_id": thread_id, "amount": amount})
        return resp.get("messages", [])

    def pending(self) -> list[dict[str, Any]]:
        """Pending (message-request) threads."""
        return self._get("/pending").get("threads", [])


def stream_events(
    base_url: str,
    on_event: Callable[[Event], None],
    stop: Callable[[], bool],
    *,
    transport: httpx2.BaseTransport | None = None,
) -> None:
    """Consume the bridge's /events SSE forever, calling `on_event` per domain event.

    Reconnects with backoff until `stop()` returns True. Runs in a daemon thread;
    `on_event` (the channel's handle) is called on that thread and must not block.
    `transport` is for tests.
    """
    base_url = base_url.rstrip("/")
    backoff = 1.0
    while not stop():
        try:
            timeout = httpx2.Timeout(10.0, read=None)
            with (
                httpx2.Client(base_url=base_url, timeout=timeout, transport=transport) as client,
                client.stream("GET", "/events") as resp,
            ):
                # The response must stay open while it is read: iterate inside the block.
                resp.raise_for_status()
                backoff = 1.0
                logger.info("Connected to bridge events at %s/events", base_url)
                for line in resp.iter_lines():
                    if stop():
                        return
                    if not line or not line.startswith("data: "):
                        continue
                    try:
                        payload = json.loads(line[len("data: ") :])
                    except json.JSONDecodeError:
                        continue
                    event = event_from_dict(payload)
                    if event is not None:
                        try:
                            on_event(event)
                        except Exception:
                            logger.exception("Channel handler failed on %s", type(event).__name__)
        except Exception:
            if stop():
                return
            logger.warning(
                "Bridge event stream dropped; reconnecting in %.0fs", backoff, exc_info=True
            )
            import time

            time.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
