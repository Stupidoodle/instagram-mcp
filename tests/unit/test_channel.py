"""Unit tests for the Claude Code channel."""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.types import Implementation, InitializeResult, ServerCapabilities

from instagram_mcp.channel import (
    CHANNEL_CAPABILITY,
    CHANNEL_METHOD,
    INSTRUCTIONS,
    Channel,
    ChannelError,
    _advertise_channel,
    _zone,
    slugify,
)
from instagram_mcp.mqtt.events import (
    MessageEvent,
    ReactionEvent,
    SeenEvent,
    ThreadEvent,
    TypingEvent,
    UnsendEvent,
)

ME = "1000"
HER = "2000"
T1 = "340282366841700000000000000000000000001"
T2 = "340282366841700000000000000000000000002"


def _describe(thread_id: str) -> tuple[str, dict[str, str]]:
    titles = {T1: "Alex Muller", T2: "Mika"}
    return titles.get(thread_id, ""), {HER: "Alex"}


def _channel(**kwargs: Any) -> tuple[Channel, list[tuple[str, dict[str, str]]]]:
    channel = Channel(self_user_id=ME, describe_thread=_describe, **kwargs)
    sent: list[tuple[str, dict[str, str]]] = []
    channel._emit = lambda content, meta: sent.append((content, meta))  # type: ignore[method-assign]
    return channel, sent


def _msg(user: str = HER, text: str | None = "hey", **kwargs: Any) -> MessageEvent:
    fields: dict[str, Any] = {
        "thread_id": T1,
        "item_id": "i1",
        "user_id": int(user),
        "text": text,
        "item_type": "text",
        "timestamp": 1_770_000_000_000_000,
    } | kwargs
    return MessageEvent(**fields)


class TestSlugify:
    def test_name(self) -> None:
        assert slugify("Alex Müller!") == "alex-muller"

    def test_digits_only_is_no_name(self) -> None:
        assert slugify("12345") == ""


class TestSubscriptions:
    def test_alias_derived_from_title(self) -> None:
        channel, _ = _channel()
        assert channel.subscribe(T1) == "alex-muller"
        assert channel.display(T1) == "alex-muller (Alex Muller)"

    def test_explicit_alias_and_resubscribe(self) -> None:
        channel, _ = _channel()
        assert channel.subscribe(T1, "Alex") == "alex"
        assert channel.subscribe(T1, "other") == "alex"

    def test_alias_conflict(self) -> None:
        channel, _ = _channel()
        channel.subscribe(T1, "alex")
        with pytest.raises(ChannelError, match="already maps"):
            channel.subscribe(T2, "alex")

    def test_derived_alias_collision_gets_thread_tail(self) -> None:
        channel = Channel(self_user_id=ME, describe_thread=lambda _t: ("Same", {}))
        assert channel.subscribe(T1) == "same"
        assert channel.subscribe(T2) == f"same-{T2[-4:]}"

    def test_untitled_thread_falls_back_to_tail(self) -> None:
        channel = Channel(self_user_id=ME, describe_thread=lambda _t: ("", {}))
        assert channel.subscribe(T1) == f"ig-{T1[-4:]}"

    def test_describe_failure_is_tolerated(self) -> None:
        def boom(_t: str) -> tuple[str, dict[str, str]]:
            raise RuntimeError("rate limited")

        channel = Channel(self_user_id=ME, describe_thread=boom)
        assert channel.subscribe(T1) == f"ig-{T1[-4:]}"

    def test_resolve(self) -> None:
        channel, _ = _channel()
        with pytest.raises(ChannelError, match="no subscribed target"):
            channel.resolve(None)
        channel.subscribe(T1, "alex")
        assert channel.resolve(None) == T1
        assert channel.resolve("alex") == T1
        assert channel.resolve(T2) == T2
        channel.subscribe(T2, "mika")
        with pytest.raises(ChannelError, match="multiple targets"):
            channel.resolve(None)
        with pytest.raises(ChannelError, match="unknown alias"):
            channel.resolve("nope")

    def test_control_thread_is_not_a_default_target(self) -> None:
        channel, _ = _channel(control_thread=T2)
        channel.subscribe(T1, "alex")
        channel.subscribe(T2, "control")
        assert channel.resolve(None) == T1
        assert any("(control)" in line for line in channel.subscriptions())

    def test_unsubscribe(self) -> None:
        channel, _ = _channel()
        channel.subscribe(T1, "alex")
        assert channel.unsubscribe("alex") == "alex"
        assert channel.subscriptions() == []
        assert channel.display(T1) == f"…{T1[-4:]}"

    def test_set_idle_requires_subscription(self) -> None:
        channel, _ = _channel()
        with pytest.raises(ChannelError):
            channel.set_idle(T1, 10)


class TestEvents:
    def test_unsubscribed_thread_is_filtered(self) -> None:
        channel, sent = _channel()
        channel.handle(_msg())
        assert sent == []

    def test_their_message(self) -> None:
        channel, sent = _channel()
        channel.subscribe(T1, "alex")
        channel.handle(_msg())
        content, meta = sent[0]
        assert content == "hey"
        assert meta["chat"] == "alex"
        assert meta["user"] == "Alex"
        assert meta["message_id"] == "i1"
        assert "is_from_me" not in meta

    def test_unknown_user_is_described_once_more(self) -> None:
        describe = MagicMock(return_value=("Alex", {}))
        channel = Channel(self_user_id=ME, describe_thread=describe)
        channel._emit = MagicMock()  # type: ignore[method-assign]
        channel.subscribe(T1)
        channel.handle(_msg(user="3000"))
        assert describe.call_count == 2
        assert channel._emit.call_args.args[1]["user"] == "3000"

    def test_media_and_view_once(self) -> None:
        channel, sent = _channel()
        channel.subscribe(T1, "alex")
        channel.handle(_msg(text=None, item_type="media"))
        channel.handle(_msg(text=None, item_type="raven_media", item_id="i2"))
        assert sent[0][1]["media_type"] == "media"
        assert "message_id=i1" in sent[0][0]
        assert sent[1][1]["view_once"] == "true"
        assert "can't be opened" in sent[1][0]

    def test_edit(self) -> None:
        channel, sent = _channel()
        channel.subscribe(T1, "alex")
        channel.handle(_msg(text="fixed", edited=True))
        channel.handle(_msg(text=None, edited=True))
        channel.handle(_msg(user=ME, text="mine", edited=True))
        assert len(sent) == 1
        assert sent[0][1]["event_type"] == "edit"
        assert sent[0][1]["target_message_id"] == "i1"
        assert sent[0][0] == "[edited → fixed]"

    def test_own_echo_is_dropped_once(self) -> None:
        channel, sent = _channel()
        channel.subscribe(T1, "alex")
        channel.expect_echo(T1, "text", "on my way")
        channel.handle(_msg(user=ME, text="on my way"))
        assert sent == []
        channel.handle(_msg(user=ME, text="on my way"))
        assert sent[0][1]["is_from_me"] == "true"

    def test_own_media_echo(self) -> None:
        channel, sent = _channel()
        channel.subscribe(T1, "alex")
        channel.expect_echo(T1, "media")
        channel.handle(_msg(user=ME, text=None, item_type="voice_media"))
        assert sent == []

    def test_expired_expectation_does_not_match(self, monkeypatch: pytest.MonkeyPatch) -> None:
        channel, sent = _channel()
        channel.subscribe(T1, "alex")
        channel.expect_echo(T1, "text", "hi")
        clock = channel._expected[T1][0].deadline + 1
        monkeypatch.setattr("instagram_mcp.channel.time.monotonic", lambda: clock)
        channel.handle(_msg(user=ME, text="hi"))
        assert sent[0][1]["is_from_me"] == "true"

    def test_debug_prefix_is_a_command(self) -> None:
        channel, sent = _channel()
        channel.subscribe(T1, "alex")
        channel.handle(_msg(user=ME, text="debug: back off"))
        assert sent[0] == (
            "back off",
            {"chat": "alex", "ts": sent[0][1]["ts"], "event_type": "command", "user": "operator"},
        )

    def test_control_thread_is_a_command(self) -> None:
        channel, sent = _channel(control_thread=T1)
        channel.subscribe(T1, "control")
        channel.handle(_msg(user=ME, text="pause alex"))
        assert sent[0][1]["event_type"] == "command"

    def test_unsend_reaction_seen_typing(self) -> None:
        channel, sent = _channel()
        channel.subscribe(T1, "alex")
        channel.handle(UnsendEvent(thread_id=T1, item_id="i1", user_id=int(HER)))
        channel.handle(ReactionEvent(T1, "i1", int(HER), "emojis", "😂"))
        channel.handle(ReactionEvent(T1, "i1", int(HER), "likes", None))
        channel.handle(ReactionEvent(T1, "i1", int(HER), "emojis", None))
        channel.handle(SeenEvent(thread_id=T1, user_id=int(HER), item_id="i1", timestamp=0))
        channel.handle(TypingEvent(thread_id=T1, user_id=int(HER), activity_status=1, ttl=0))
        channel.handle(TypingEvent(thread_id=T1, user_id=int(HER), activity_status=0, ttl=0))
        channel.handle(ThreadEvent(thread_id=T1, op="replace", path="/x"))
        types = [meta["event_type"] for _, meta in sent]
        assert types == [
            "unsend",
            "reaction",
            "reaction",
            "reaction",
            "read",
            "typing",
            "typing_stopped",
        ]
        assert [c for c, _ in sent[1:4]] == ["[reacted 😂]", "[reacted ❤️]", "[removed reaction]"]

    def test_own_side_events(self) -> None:
        channel, sent = _channel()
        channel.subscribe(T1, "alex")
        channel.expect_echo(T1, "unsend", "i1")
        channel.expect_echo(T1, "reaction", "i1")
        channel.handle(UnsendEvent(thread_id=T1, item_id="i1", user_id=int(ME)))
        channel.handle(ReactionEvent(T1, "i1", int(ME), "emojis", "🔥"))
        channel.handle(SeenEvent(thread_id=T1, user_id=int(ME), item_id="i1", timestamp=0))
        channel.handle(TypingEvent(thread_id=T1, user_id=int(ME), activity_status=1, ttl=0))
        assert sent == []
        channel.handle(ReactionEvent(T1, "i1", int(ME), "emojis", "🔥"))
        assert sent[0][1]["is_from_me"] == "true"

    def test_own_reaction_memory(self) -> None:
        channel, _ = _channel()
        channel.remember_reaction(T1, "i1", "🔥")
        assert channel.own_reaction(T1, "i1") == "🔥"
        channel.remember_reaction(T1, "i1", "")
        assert channel.own_reaction(T1, "i1") is None


class TestIdle:
    @pytest.fixture(autouse=True)
    def _exact_clock(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Nudges fire exactly on minute boundaries. An integer-valued clock keeps that
        # arithmetic exact whatever the machine's uptime (a fresh CI VM starts near 0).
        monkeypatch.setattr(time, "monotonic", lambda: 1_000_000.0)

    def test_nudge_cadence(self) -> None:
        channel, _ = _channel(idle_minutes=5)
        channel.subscribe(T1, "alex")
        start = channel._chats[T1].last_activity
        assert channel.idle_nudges(start + 4 * 60) == []
        ((content, meta),) = channel.idle_nudges(start + 5 * 60)
        assert meta["event_type"] == "idle"
        assert meta["minutes_idle"] == "5"
        assert "5 minutes" in content
        assert channel.idle_nudges(start + 9 * 60) == []
        assert len(channel.idle_nudges(start + 10 * 60)) == 1

    def test_backoff_after_thirty_quiet_minutes(self) -> None:
        channel, _ = _channel(idle_minutes=5, idle_backoff_after_minutes=30, idle_max_minutes=60)
        channel.subscribe(T1, "alex")
        start = channel._chats[T1].last_activity
        fired = [m for m in range(0, 400) if channel.idle_nudges(start + m * 60)]
        # every 5 min up to 30, then the gap doubles (10, 20, 40) and caps at 60
        assert fired == [5, 10, 15, 20, 25, 30, 40, 60, 100, 160, 220, 280, 340]

    def test_backoff_resets_on_activity(self) -> None:
        channel, _ = _channel(idle_minutes=5)
        channel.subscribe(T1, "alex")
        start = channel._chats[T1].last_activity
        for m in range(0, 45):
            channel.idle_nudges(start + m * 60)
        assert channel._chats[T1].nudge_interval == 20
        channel.handle(_msg())
        assert channel._chats[T1].nudge_interval is None

    def test_idle_meta_announces_next_nudge(self) -> None:
        channel, _ = _channel(idle_minutes=5)
        channel.subscribe(T1, "alex")
        ((content, meta),) = channel.idle_nudges(channel._chats[T1].last_activity + 5 * 60)
        assert meta["next_nudge_minutes"] == "5"
        assert "next nudge in 5 min" in content

    def test_disabled_and_control(self) -> None:
        channel, _ = _channel(idle_minutes=0, control_thread=T2)
        channel.subscribe(T1, "alex")
        channel.subscribe(T2, "control")
        assert channel.idle_nudges(channel._chats[T1].last_activity + 3600) == []

    def test_set_idle_until_they_write(self) -> None:
        channel, _ = _channel(idle_minutes=5)
        channel.subscribe(T1, "alex")
        channel.set_idle(T1, 60)
        start = channel._chats[T1].last_activity
        assert channel.idle_nudges(start + 30 * 60) == []
        channel.handle(_msg())
        assert channel._chats[T1].idle_override is None

    def test_own_message_keeps_override(self) -> None:
        channel, _ = _channel()
        channel.subscribe(T1, "alex")
        channel.set_idle(T1, 0)
        channel.handle(_msg(user=ME, text="from my phone"))
        assert channel._chats[T1].idle_override == 0

    def test_clock_zone(self) -> None:
        assert _zone("Europe/Zurich") is not None
        assert _zone("Mars/Olympus") is None
        assert _zone("../etc") is None
        assert _zone(None) is None


class TestSessionPush:
    def test_advertise_on_model(self) -> None:
        result = InitializeResult(
            protocol_version="2025-11-25",
            capabilities=ServerCapabilities(experimental={"other": {}}),
            server_info=Implementation(name="t", version="1"),
        )
        advertised = _advertise_channel(result)
        assert isinstance(advertised, InitializeResult)
        assert advertised.capabilities.experimental == {"other": {}, CHANNEL_CAPABILITY: {}}

    def test_advertise_on_dict(self) -> None:
        advertised = _advertise_channel({"protocolVersion": "x"})
        assert advertised == {
            "protocolVersion": "x",
            "capabilities": {"experimental": {CHANNEL_CAPABILITY: {}}},
        }
        assert _advertise_channel(None) is None

    async def test_middleware_attaches_and_flushes(self) -> None:
        channel = Channel(self_user_id=ME, describe_thread=_describe)
        channel.subscribe(T1, "alex")
        channel.handle(_msg())  # before the handshake: held
        session = MagicMock()
        session.send_notification = AsyncMock()

        init_ctx = MagicMock(method="initialize")
        caps = ServerCapabilities()
        init_result = InitializeResult(
            protocol_version="2025-11-25",
            capabilities=caps,
            server_info=Implementation(name="t", version="1"),
        )
        result = await channel.middleware(init_ctx, AsyncMock(return_value=init_result))
        assert CHANNEL_CAPABILITY in result.capabilities.experimental  # type: ignore[union-attr]

        ready_ctx = MagicMock(method="notifications/initialized", session=session)
        await channel.middleware(ready_ctx, AsyncMock(return_value=None))
        await asyncio.sleep(0)
        notification = session.send_notification.await_args.args[0]
        assert notification.method == CHANNEL_METHOD
        assert notification.params["content"] == "hey"
        assert notification.params["meta"]["chat"] == "alex"

        other = await channel.middleware(
            MagicMock(method="tools/list"), AsyncMock(return_value={"tools": []})
        )
        assert other == {"tools": []}
        assert channel._idle_task is not None
        channel._idle_task.cancel()

    async def test_emit_after_attach_crosses_threads(self) -> None:
        channel = Channel(self_user_id=ME, describe_thread=_describe)
        channel.subscribe(T1, "alex")
        session = MagicMock()
        session.send_notification = AsyncMock()
        channel.attach(session)
        await asyncio.to_thread(channel.handle, _msg(text="from the mqtt thread"))
        for _ in range(5):
            await asyncio.sleep(0)
        assert (
            session.send_notification.await_args.args[0].params["content"] == "from the mqtt thread"
        )
        assert channel._idle_task is not None
        channel._idle_task.cancel()

    async def test_send_without_session_is_a_noop(self) -> None:
        channel = Channel(self_user_id=ME, describe_thread=_describe)
        await channel._send("x", {})


def test_instructions_fit_claude_codes_limit() -> None:
    """Claude Code truncates server instructions past 2048 characters."""
    assert len(INSTRUCTIONS) <= 2048


class TestActionLog:
    def test_action_log_is_a_notice_not_their_turn(self) -> None:
        channel, sent = _channel(idle_minutes=5)
        channel.subscribe(T1, "alex")
        channel.set_idle(T1, 60)
        channel.handle(_msg(text="You missed a video chat", item_type="action_log"))
        content, meta = sent[-1]
        assert content == "You missed a video chat"
        assert meta["event_type"] == "notice"
        assert meta["user"] == "Alex"
        assert channel._chats[T1].idle_override is not None


class TestLink:
    def test_link_message_carries_its_url(self) -> None:
        channel, sent = _channel()
        channel.subscribe(T1, "alex")
        channel.handle(
            _msg(
                text="survey: https://example.com/s",
                item_type="link",
                link_url="https://example.com/s",
                link_title="Survey",
            )
        )
        content, meta = sent[-1]
        assert content == "survey: https://example.com/s"
        assert meta["link_url"] == "https://example.com/s"
        assert meta["link_title"] == "Survey"
