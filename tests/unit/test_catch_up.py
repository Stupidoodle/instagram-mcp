"""A session catches up on what it missed, and only counts what Claude Code received."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from instagram_mcp.catch_up import CatchUp, ChannelState, history_event, state_path
from instagram_mcp.mqtt.events import MessageEvent, TypingEvent

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    import pytest

    from instagram_mcp.mqtt.events import Event


def _msg(item_id: str, thread: str = "t1", edited: bool = False) -> MessageEvent:
    return MessageEvent(thread, item_id, 42, "hi", "text", 1, edited=edited)


def _item(item_id: str, kind: str = "text") -> dict[str, Any]:
    return {
        "message_id": item_id,
        "user_id": "42",
        "text": f"msg {item_id}",
        "media_type": kind,
        "timestamp": "2026-09-28T00:05:00+02:00",
    }


class Sink:
    """Stands in for channel.handle: collects events, and 'sends' them when told."""

    def __init__(self, send: bool = True) -> None:
        self.events: list[Event] = []
        self.send = send

    def __call__(self, event: Event, on_sent: Callable[[], None]) -> None:
        self.events.append(event)
        if self.send:
            on_sent()


def _catch_up(
    tmp_path: Path, history: dict[str, list[dict[str, Any]]], sink: Sink
) -> tuple[CatchUp, ChannelState]:
    state = ChannelState(tmp_path / "state.json")
    return CatchUp(state, lambda t, _n: history.get(t, []), lambda: list(history), sink), state


def test_the_position_moves_only_once_claude_code_got_the_event(tmp_path: Path) -> None:
    held = Sink(send=False)
    catch_up, state = _catch_up(tmp_path, {}, held)
    catch_up.deliver(_msg("100"), "b1-7")
    assert held.events == [_msg("100")]
    assert state.last_event_id is None  # never written to Claude Code: replay it next time

    sent = Sink()
    catch_up, state = _catch_up(tmp_path, {}, sent)
    catch_up.deliver(_msg("100"), "b1-7")
    catch_up.deliver(TypingEvent("t1", 42, 1, 1), "b1-8")
    assert state.last_event_id == "b1-8"
    assert state.last_item("t1") == "100"
    reloaded = ChannelState(tmp_path / "state.json")  # survives a relaunch
    assert (reloaded.last_event_id, reloaded.last_item("t1")) == ("b1-8", "100")


def test_the_position_never_moves_back(tmp_path: Path) -> None:
    state = ChannelState(tmp_path / "s.json")
    state.record(event_id="b1-9", thread_id="t1", item_id="200")
    state.record(event_id="b1-3", thread_id="t1", item_id="150")
    assert (state.last_event_id, state.last_item("t1")) == ("b1-9", "200")
    state.record(event_id="b2-1")  # a new bridge boot
    assert state.last_event_id == "b2-1"


def test_a_message_is_delivered_once(tmp_path: Path) -> None:
    sink = Sink()
    catch_up, _ = _catch_up(tmp_path, {}, sink)
    catch_up.deliver(_msg("100"), "b1-1")
    catch_up.deliver(_msg("100"), "b1-2")  # same message again (replay after a backfill)
    catch_up.deliver(_msg("100", edited=True), "b1-3")  # an edit is news
    assert len(sink.events) == 2


def test_a_gap_is_filled_from_history_newer_than_the_last_message(tmp_path: Path) -> None:
    history = {"t1": [_item("103"), _item("102"), _item("101", "action_log"), _item("100")]}
    sink = Sink()
    catch_up, state = _catch_up(tmp_path, history, sink)
    state.record(thread_id="t1", item_id="100")
    assert catch_up.fill_gap() == 2
    assert [e.item_id for e in sink.events if isinstance(e, MessageEvent)] == ["102", "103"]
    assert all(isinstance(e, MessageEvent) and e.backfilled for e in sink.events)
    assert state.last_item("t1") == "103"
    assert catch_up.fill_gap() == 0  # nothing new the second time


def test_the_first_run_only_marks_the_spot(tmp_path: Path) -> None:
    sink = Sink()
    catch_up, state = _catch_up(tmp_path, {"t1": [_item("103"), _item("102")]}, sink)
    assert catch_up.fill_gap() == 0
    assert sink.events == []
    assert state.last_item("t1") == "103"


def test_a_failing_history_skips_that_chat(tmp_path: Path) -> None:
    def history(_thread: str, _amount: int) -> list[dict[str, Any]]:
        raise RuntimeError("bridge down")

    state = ChannelState(tmp_path / "s.json")
    catch_up = CatchUp(state, history, lambda: ["t1"], Sink())
    assert catch_up.fill_gap() == 0


def test_history_items_become_backfilled_events() -> None:
    event = history_event("t1", _item("7", "voice"))
    assert event is not None
    assert (event.item_type, event.backfilled, event.user_id) == ("voice_media", True, 42)
    assert history_event("t1", _item("7", "placeholder")) is None


def test_a_broken_state_file_starts_fresh(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    path.write_text("{not json")
    assert ChannelState(path).last_event_id is None


def test_state_files_are_per_folder_and_chats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    a = state_path(["t1"], folder="/p/christine-instagram")
    assert a == state_path(["t1"], folder="/p/christine-instagram")
    assert a != state_path(["t2"], folder="/p/christine-instagram")
    assert a != state_path(["t1"], folder="/p/ly-whatsapp")
    assert a.parent == tmp_path / "instagram-mcp"
    json.dumps(str(a))
