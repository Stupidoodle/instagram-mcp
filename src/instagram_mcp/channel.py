"""Claude Code channel: pushes Instagram DM events into the live session.

Mirrors the WhatsApp channel server. Chats are addressed by a short alias,
only subscribed threads deliver events, and an idle heartbeat nudges quiet
chats. Each event goes out as a ``notifications/claude/channel`` notification
on the connection's standalone stream, so it reaches Claude between tool
calls without any polling.

Start Claude Code with ``--dangerously-load-development-channels server:instagram``.
"""

from __future__ import annotations

import asyncio
import collections
import logging
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final, Literal, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import anyio
from mcp.server.mcpserver import MCPServer
from mcp.server.stdio import stdio_server
from mcp.shared.message import SessionMessage
from mcp.types import (
    METHOD_NOT_FOUND,
    DiscoverResult,
    ErrorData,
    InitializeResult,
    JSONRPCError,
    JSONRPCRequest,
    Notification,
)
from mcp.types.version import MODERN_PROTOCOL_VERSIONS

from instagram_mcp.mqtt.events import (
    MessageEvent,
    ReactionEvent,
    SeenEvent,
    TypingEvent,
    UnsendEvent,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
    from mcp.server.session import ServerSession
    from mcp.types import ServerNotification

    from instagram_mcp.mqtt.events import Event

logger = logging.getLogger("instagram_mcp.channel")


def _media_meta(event: MessageEvent) -> dict[str, str]:
    """The downloaded file, transcript and any error of an inbound media message."""
    fields = {
        "media_path": event.media_path,
        "transcript": event.transcript,
        "media_error": event.media_error,
    }
    return {key: value for key, value in fields.items() if value}


def _media_label(event: MessageEvent) -> str:
    """What a persona sees for a media message without a caption."""
    if event.item_type == "voice_media":
        return f"[voice note] {event.transcript}" if event.transcript else "[voice note]"
    if event.item_type == "media" and event.media_path:
        return "[video]" if event.media_path.endswith((".mp4", ".mov")) else "[photo]"
    return f"[{event.item_type}: message_id={event.item_id}]"


def _now() -> float:
    """The monotonic clock, looked up at call time so tests can pin it."""
    return time.monotonic()


CHANNEL_CAPABILITY: Final = "claude/channel"
CHANNEL_METHOD: Final = "notifications/claude/channel"

# Own sends are echoed back over MQTT; an echo we expect is dropped instead of
# looking like the owner texting from their phone.
_ECHO_WINDOW_SECONDS = 60
# Events that arrive before the client finishes the handshake are held briefly.
_PENDING_MAX = 100

INSTRUCTIONS = """Instagram DM channel. Conversations are addressed by a short ALIAS (e.g. "alex").
You never type a raw thread id except once, in subscribe.

INCOMING EVENTS (subscribed chats only) arrive as <channel source="instagram" chat="<alias>" ...>:
- Message: attributes chat, user, message_id, ts. Media adds media_type and media_path
  (Read it); voice notes come as their transcript. is_from_me="true": the owner
  sent it from their phone. A message event means it's your turn: reply right away.
- View-once: view_once="true" is a disappearing photo/video. It can't be opened here;
  never pretend you saw it.
- Edit / unsend / reaction: event_type="edit" | "unsend" | "reaction" with
  target_message_id; content is the new text (edit) or the emoji (reaction).
- Read / typing: event_type="read", "typing" or "typing_stopped".
- Notice: event_type="notice" is a system line (missed call...), not their turn.
- Idle: event_type="idle" minutes_idle="N" next_nudge_minutes="M" clock="<local time>";
  the chat has been quiet. Use the clock to judge the hour. Re-engage only if your
  rules say so. Nudges back off after 30 quiet minutes.
- Operator command: event_type="command" is the operator instructing YOU (control
  chat or a "debug:" message). Carry it out; never reply to it in the chat.

TOOLS (address by alias, or omit "to" for the sole subscribed target):
- reply(text, to?): send a text message
- send_file(file_path, to?): send a photo or video
- send_audio(file_path, to?): send a voice message
- send_typing(to?, composing?): optional "typing..." indicator
- mark_read(message_ids, to?): optional read receipt
- download_attachment(message_id, to?): fetch older media
- get_message_ids(to?, filter?, limit?): your OWN recent messages + ids (for unsend)
- unsend(message_id, to?): take back one of YOUR messages
- react(message_id, emoji, to?): react with an emoji ("" removes yours)
- set_idle(minutes, to?): idle-nudge cadence (0 pauses; resets when they write)
- subscribe(chat_id, alias?), unsubscribe(to), list_subscriptions()"""


class ChannelNotification(Notification[dict[str, Any], Literal["notifications/claude/channel"]]):
    """Claude Code renders this vendor notification as a ``<channel>`` event."""

    method: Literal["notifications/claude/channel"] = CHANNEL_METHOD


class ChannelError(ValueError):
    """A tool addressed a chat the channel can't resolve."""


@dataclass
class _Chat:
    thread_id: str
    alias: str
    name: str = ""
    user_names: dict[str, str] = field(default_factory=dict)
    last_activity: float = field(default_factory=_now)
    idle_override: float | None = None
    last_nudge: float | None = None
    nudge_interval: float | None = None  # minutes; grows once the chat has gone quiet


@dataclass
class _Expected:
    kind: str  # "text", "media", "reaction" or "unsend"
    key: str | None
    deadline: float


def slugify(name: str) -> str:
    """Turn a display name into an alias candidate ("Alex Muller" → "alex-muller")."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = "-".join("".join(c if c.isalnum() else " " for c in ascii_name.lower()).split())
    return "" if slug.isdigit() else slug


class Channel:
    """Routes MQTT events for subscribed threads into the Claude Code session.

    Thread-safe: ``handle()`` runs on the MQTT reader thread, tools run in
    worker threads, and pushes are scheduled onto the server's event loop.
    """

    def __init__(
        self,
        *,
        self_user_id: str,
        describe_thread: Callable[[str], tuple[str, dict[str, str]]],
        idle_minutes: float = 5,
        idle_backoff_after_minutes: float = 30,
        idle_max_minutes: float = 240,
        control_thread: str = "",
        debug_prefix: str = "debug:",
        tz: str | None = None,
    ) -> None:
        """Create the channel.

        Args:
            self_user_id: The logged-in account's user id (own events are echoes).
            describe_thread: Returns (title, {user_id: display name}) for a thread.
            idle_minutes: Quiet minutes before an idle nudge; 0 disables nudges.
            idle_backoff_after_minutes: Once a chat has been quiet this long, each
                further nudge doubles the gap to the next one.
            idle_max_minutes: Upper bound for that growing gap.
            control_thread: Thread id of an operator control chat, if any.
            debug_prefix: Own messages starting with this are operator commands.
            tz: IANA time zone for the idle event's clock (default: host zone).
        """
        self._self_user_id = self_user_id
        self._describe_thread = describe_thread
        self._idle_minutes = idle_minutes
        self._idle_backoff_after = idle_backoff_after_minutes
        self._idle_max = idle_max_minutes
        self._control_thread = control_thread
        self._debug_prefix = debug_prefix
        self._tz = _zone(tz)

        self._lock = threading.RLock()
        self._chats: dict[str, _Chat] = {}
        self._aliases: dict[str, str] = {}
        self._expected: dict[str, list[_Expected]] = collections.defaultdict(list)
        self._own_reactions: dict[tuple[str, str], str] = {}

        self._session: ServerSession | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pending: collections.deque[tuple[str, dict[str, str]]] = collections.deque(
            maxlen=_PENDING_MAX
        )
        self._idle_task: asyncio.Task[None] | None = None

    # ── Subscriptions ────────────────────────────────────────────────────────

    def subscribe(self, thread_id: str, alias: str | None = None) -> str:
        """Stream events for a thread under a short alias; returns the alias."""
        thread_id = thread_id.strip()
        with self._lock:
            if thread_id in self._chats:
                return self._chats[thread_id].alias
        title, names = self._safe_describe(thread_id)
        with self._lock:
            if alias:
                final = "-".join(alias.strip().lower().split())
                owner = self._aliases.get(final)
                if owner is not None and owner != thread_id:
                    taken = self.display(owner)
                    msg = f'alias "{final}" already maps to {taken}; pass a distinct alias'
                    raise ChannelError(msg)
            else:
                final = self._derive_alias(thread_id, title)
            self._chats[thread_id] = _Chat(thread_id, final, title, names)
            self._aliases[final] = thread_id
        logger.info("Subscribed thread …%s as %r", thread_id[-4:], final)
        return final

    def unsubscribe(self, to: str) -> str:
        """Stop streaming a chat; returns its alias."""
        thread_id = self.resolve(to)
        with self._lock:
            chat = self._chats.pop(thread_id, None)
            if chat is not None:
                self._aliases.pop(chat.alias, None)
        return chat.alias if chat else thread_id

    def subscriptions(self) -> list[str]:
        """Human-readable list of subscribed chats."""
        with self._lock:
            return [
                f"{c.alias} → …{c.thread_id[-4:]}"
                + (" (control)" if c.thread_id == self._control_thread else "")
                for c in self._chats.values()
            ]

    def resolve(self, to: str | None) -> str:
        """Resolve a tool's optional ``to`` (alias or subscribed thread id) to a thread id.

        Raises:
            ChannelError: If nothing or several chats match.
        """
        with self._lock:
            if not to:
                targets = [t for t in self._chats if t != self._control_thread]
                if len(targets) == 1:
                    return targets[0]
                if not targets:
                    msg = "no subscribed target — subscribe(chat_id, alias) first"
                    raise ChannelError(msg)
                aliases = ", ".join(self._chats[t].alias for t in targets)
                msg = f"multiple targets — pass to:<alias> ({aliases})"
                raise ChannelError(msg)
            if to in self._aliases:
                return self._aliases[to]
            if to in self._chats or to.isdigit():
                return to
            known = ", ".join(self._aliases) or "(none)"
            msg = f'unknown alias "{to}"; known: {known}'
            raise ChannelError(msg)

    def display(self, thread_id: str) -> str:
        """``alias (name)`` for a thread, for tool results."""
        with self._lock:
            chat = self._chats.get(thread_id)
        if chat is None:
            return f"…{thread_id[-4:]}"
        return f"{chat.alias} ({chat.name})" if chat.name else chat.alias

    def set_idle(self, thread_id: str, minutes: float) -> None:
        """Override the idle threshold for a chat until they next write."""
        with self._lock:
            chat = self._chats.get(thread_id)
            if chat is None:
                msg = "not subscribed"
                raise ChannelError(msg)
            chat.idle_override = minutes
            chat.last_nudge = None
            chat.nudge_interval = None

    @property
    def idle_minutes(self) -> float:
        """The default idle threshold in minutes."""
        return self._idle_minutes

    # ── Echo suppression ─────────────────────────────────────────────────────

    def expect_echo(self, thread_id: str, kind: str, key: str | None = None) -> None:
        """Register an action we're about to take so its MQTT echo is dropped.

        Call before sending: the echo can arrive before the HTTP call returns.
        """
        with self._lock:
            self._expected[thread_id].append(
                _Expected(kind, key, time.monotonic() + _ECHO_WINDOW_SECONDS)
            )

    def remember_reaction(self, thread_id: str, item_id: str, emoji: str) -> None:
        """Track our own reaction so removing it knows which emoji to send."""
        with self._lock:
            if emoji:
                self._own_reactions[(thread_id, item_id)] = emoji
            else:
                self._own_reactions.pop((thread_id, item_id), None)

    def own_reaction(self, thread_id: str, item_id: str) -> str | None:
        """The emoji we last reacted with on a message, if known."""
        with self._lock:
            return self._own_reactions.get((thread_id, item_id))

    def _consume_expected(self, thread_id: str, kind: str, key: str | None) -> bool:
        with self._lock:
            now = time.monotonic()
            queue = [e for e in self._expected.get(thread_id, []) if e.deadline > now]
            for i, expected in enumerate(queue):
                if expected.kind == kind and (expected.key is None or expected.key == key):
                    del queue[i]
                    self._expected[thread_id] = queue
                    return True
            self._expected[thread_id] = queue
            return False

    # ── MQTT → channel ───────────────────────────────────────────────────────

    def handle(self, event: Event) -> None:
        """Convert one MQTT event into a channel push (MQTT reader thread)."""
        with self._lock:
            chat = self._chats.get(event.thread_id)
        if chat is None:
            logger.debug(
                "[filtered] %s from …%s (not subscribed)",
                type(event).__name__,
                event.thread_id[-4:],
            )
            return

        built = self._build(chat, event)
        if built is None:
            return
        content, meta = built
        their_message = (
            isinstance(event, MessageEvent)
            and not event.edited
            and "is_from_me" not in meta
            and meta.get("event_type") not in {"command", "notice"}
        )
        with self._lock:
            chat.last_activity = time.monotonic()
            chat.last_nudge = None
            chat.nudge_interval = None
            if their_message:
                chat.idle_override = None  # their message resets set_idle
        self._emit(content, meta)

    def _build(self, chat: _Chat, event: Event) -> tuple[str, dict[str, str]] | None:  # noqa: PLR0911
        from_me = str(getattr(event, "user_id", "")) == self._self_user_id
        meta = {"chat": chat.alias, "ts": datetime.now(UTC).isoformat()}
        user = self._user_name(chat, getattr(event, "user_id", 0))

        if isinstance(event, MessageEvent):
            return self._build_message(chat, event, meta, user=user, from_me=from_me)
        if isinstance(event, UnsendEvent):
            if from_me and self._consume_expected(chat.thread_id, "unsend", event.item_id):
                return None
            meta |= {"event_type": "unsend", "target_message_id": event.item_id, "user": user}
            return "[unsent a message]", meta
        if isinstance(event, ReactionEvent):
            key = event.item_id
            if from_me and self._consume_expected(chat.thread_id, "reaction", key):
                return None
            meta |= {"event_type": "reaction", "target_message_id": key, "user": user}
            if from_me:
                meta["is_from_me"] = "true"
            emoji = event.emoji or ("❤️" if event.reaction_type == "likes" else "")
            return (f"[reacted {emoji}]" if emoji else "[removed reaction]"), meta
        if from_me:
            return None  # own read receipts and typing
        if isinstance(event, SeenEvent):
            meta |= {"event_type": "read", "user": user}
            return "[read receipt]", meta
        if isinstance(event, TypingEvent):
            typing = event.activity_status != 0
            meta |= {"event_type": "typing" if typing else "typing_stopped", "user": user}
            return ("[typing...]" if typing else "[stopped typing]"), meta
        return None

    def _build_message(  # noqa: PLR0911
        self,
        chat: _Chat,
        event: MessageEvent,
        meta: dict[str, str],
        *,
        user: str,
        from_me: bool,
    ) -> tuple[str, dict[str, str]] | None:
        text = event.text or ""
        if event.timestamp:
            meta["ts"] = datetime.fromtimestamp(event.timestamp / 1_000_000, UTC).isoformat()

        # A system line (missed call, theme change...), not something anyone said.
        if event.item_type == "action_log":
            return text or "[notice]", meta | {"event_type": "notice", "user": user}

        if from_me and not event.edited:
            kind = "text" if event.item_type == "text" else "media"
            if self._consume_expected(chat.thread_id, kind, text if kind == "text" else None):
                return None
            is_control = chat.thread_id == self._control_thread
            if is_control or text.startswith(self._debug_prefix):
                command = text.removeprefix(self._debug_prefix).strip()
                return command, meta | {"event_type": "command", "user": "operator"}

        if event.edited:
            if from_me or event.text is None:
                return None
            meta |= {"event_type": "edit", "target_message_id": event.item_id, "user": user}
            return f"[edited → {text}]", meta

        meta |= {"user": user, "message_id": event.item_id}
        if from_me:
            meta["is_from_me"] = "true"
        if event.link_url:
            meta["link_url"] = event.link_url
        if event.link_title:
            meta["link_title"] = event.link_title
        if event.item_type == "raven_media":
            meta["view_once"] = "true"
            return f"[{user} sent a view-once photo/video — it can't be opened here]", meta
        if event.item_type not in {"text", "unknown"}:
            meta["media_type"] = event.item_type
            meta |= _media_meta(event)
            return text or _media_label(event), meta
        return text, meta

    def _user_name(self, chat: _Chat, user_id: int | str) -> str:
        uid = str(user_id)
        if uid == self._self_user_id:
            return "me"
        if uid not in chat.user_names:
            _, names = self._safe_describe(chat.thread_id)
            with self._lock:
                chat.user_names.update(names)
        return chat.user_names.get(uid, uid)

    def _safe_describe(self, thread_id: str) -> tuple[str, dict[str, str]]:
        try:
            return self._describe_thread(thread_id)
        except Exception:
            logger.warning("Could not describe thread …%s", thread_id[-4:], exc_info=True)
            return "", {}

    def _derive_alias(self, thread_id: str, title: str) -> str:
        tail = thread_id[-4:]
        base = slugify(title) or f"ig-{tail}"
        candidates = [base, f"{base}-{tail}"]
        candidates += [f"{base}-{i}" for i in range(2, 100)]
        return next(c for c in candidates if self._aliases.get(c, thread_id) == thread_id)

    # ── Session + push ───────────────────────────────────────────────────────

    async def middleware(
        self, ctx: ServerRequestContext[Any, Any], call_next: CallNext
    ) -> HandlerResult:
        """Server middleware: advertise the channel and attach once the client is ready.

        Handshake era: advertise on ``initialize``, attach on
        ``notifications/initialized``. 2026-07-28+ has no handshake: advertise on
        ``server/discover`` and attach on the first request after it, so no push
        can reach the client before it has seen the capability.
        """
        result = await call_next(ctx)
        if ctx.method in ("initialize", "server/discover"):
            return _advertise_channel(result)
        if ctx.method == "notifications/initialized" or (
            self._session is None and ctx.protocol_version in MODERN_PROTOCOL_VERSIONS
        ):
            self.attach(ctx.session)
        return result

    def attach(self, session: ServerSession) -> None:
        """Start pushing on this session (called on the server's event loop)."""
        self._session = session
        self._loop = asyncio.get_running_loop()
        while self._pending:
            content, meta = self._pending.popleft()
            self._loop.create_task(self._send(content, meta))
        if self._idle_task is None or self._idle_task.done():
            self._idle_task = self._loop.create_task(self._idle_heartbeat())
        logger.info("Channel attached (%d subscriptions)", len(self._chats))

    def _emit(self, content: str, meta: dict[str, str]) -> None:
        """Push from any thread; held until the session attaches."""
        loop = self._loop
        if loop is None or loop.is_closed():
            self._pending.append((content, meta))
            return
        asyncio.run_coroutine_threadsafe(self._send(content, meta), loop)

    async def _send(self, content: str, meta: dict[str, str]) -> None:
        session = self._session
        if session is None:
            return
        notification = ChannelNotification(
            method=CHANNEL_METHOD, params={"content": content, "meta": meta}
        )
        # ServerSession's typed union has no vendor notifications; it only needs
        # a model with method + params and writes it to the standalone stream.
        await session.send_notification(cast("ServerNotification", notification))

    # ── Idle heartbeat ───────────────────────────────────────────────────────

    def _effective_idle(self, chat: _Chat) -> float:
        return chat.idle_override if chat.idle_override is not None else self._idle_minutes

    def idle_nudges(self, now: float | None = None) -> list[tuple[str, dict[str, str]]]:
        """Chats that are due an idle nudge now; marks them nudged.

        The first nudge comes after the idle threshold and repeats at that
        cadence. Once the chat has been quiet for ``idle_backoff_after_minutes``,
        every nudge doubles the gap to the next one, up to ``idle_max_minutes``.
        Any activity in the chat resets the cadence.
        """
        now = time.monotonic() if now is None else now
        due: list[tuple[str, dict[str, str]]] = []
        with self._lock:
            for chat in self._chats.values():
                if chat.thread_id == self._control_thread:
                    continue
                base = self._effective_idle(chat)
                quiet = now - chat.last_activity
                if base <= 0 or quiet < base * 60:
                    continue
                interval = chat.nudge_interval or base
                if chat.last_nudge is not None and now - chat.last_nudge < interval * 60:
                    continue
                chat.last_nudge = now
                if quiet >= self._idle_backoff_after * 60:
                    interval = min(interval * 2, max(self._idle_max, base))
                chat.nudge_interval = interval
                minutes = round(quiet / 60)
                clock = self._clock()
                meta = {
                    "chat": chat.alias,
                    "event_type": "idle",
                    "minutes_idle": str(minutes),
                    "next_nudge_minutes": f"{interval:g}",
                    "ts": datetime.now(UTC).isoformat(),
                    "clock": clock,
                }
                content = (
                    f"[idle: {minutes} minutes since last activity — now {clock}; "
                    f"next nudge in {interval:g} min]"
                )
                due.append((content, meta))
        return due

    async def _idle_heartbeat(self) -> None:
        while True:
            await asyncio.sleep(60)
            for content, meta in self.idle_nudges():
                await self._send(content, meta)

    def _clock(self) -> str:
        return datetime.now(self._tz).strftime("%a, %d/%m/%Y, %H:%M %Z")


def _zone(tz: str | None) -> ZoneInfo | None:
    if not tz:
        return None
    try:
        return ZoneInfo(tz)
    except ZoneInfoNotFoundError, ValueError:
        logger.warning("Unknown time zone %r, using the host zone", tz)
        return None


def _advertise_channel(result: HandlerResult) -> HandlerResult:
    """Add ``experimental["claude/channel"]`` to an initialize or discover result."""
    if isinstance(result, InitializeResult | DiscoverResult):
        caps = result.capabilities
        experimental = {**(caps.experimental or {}), CHANNEL_CAPABILITY: {}}
        return result.model_copy(
            update={"capabilities": caps.model_copy(update={"experimental": experimental})}
        )
    if isinstance(result, dict):
        caps_dict = result.setdefault("capabilities", {})
        caps_dict.setdefault("experimental", {})[CHANNEL_CAPABILITY] = {}
    return result


# ── Transport ────────────────────────────────────────────────────────────────


class ChannelMCPServer(MCPServer):
    """An MCPServer that serves stdio in the handshake era Claude Code channels need.

    Claude Code only delivers channel notifications on handshake-era
    connections; at 2026-07-28 it logs "no unsolicited notification path" and
    drops them. It opens with an enveloped ``server/discover`` and falls back to
    ``initialize`` on METHOD_NOT_FOUND, which is what handshake-only servers
    (like the TypeScript WhatsApp channel) answer. This answers that opening
    probe the same way, so the SDK picks the era from the ``initialize``.
    """

    async def run_stdio_async(self) -> None:
        """Run over stdio, declining the modern opening probe."""
        async with stdio_server() as (read_stream, write_stream):
            send, receive = anyio.create_memory_object_stream[SessionMessage | Exception](0)

            async def pump() -> None:
                opened = False
                async with send:
                    async for item in read_stream:
                        request = _request_of(item)
                        if request is not None and not opened:
                            opened = request.method != "server/discover"
                            if not opened:
                                await write_stream.send(_method_not_found(request))
                                continue
                        await send.send(item)

            async with anyio.create_task_group() as tg:
                tg.start_soon(pump)
                lowlevel = self._lowlevel_server
                await lowlevel.run(receive, write_stream, lowlevel.create_initialization_options())
                tg.cancel_scope.cancel()


def _request_of(item: SessionMessage | Exception) -> JSONRPCRequest | None:
    if isinstance(item, SessionMessage) and isinstance(item.message, JSONRPCRequest):
        return item.message
    return None


def _method_not_found(request: JSONRPCRequest) -> SessionMessage:
    error = ErrorData(code=METHOD_NOT_FOUND, message="Method not found")
    return SessionMessage(JSONRPCError(jsonrpc="2.0", id=request.id, error=error))
