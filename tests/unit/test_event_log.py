"""The bridge numbers its events and replays what a reconnecting client missed."""

import json

from instagram_mcp.event_log import EventLog


def test_frames_carry_boot_and_sequence() -> None:
    log = EventLog(keep=10, boot="b1")
    assert log.record('{"a": 1}') == 'id: b1-1\ndata: {"a": 1}\n\n'
    assert log.record('{"a": 2}').startswith("id: b1-2\n")


def test_replay_after_the_last_seen_event() -> None:
    log = EventLog(keep=10, boot="b1")
    frames = [log.record(f'{{"n": {n}}}') for n in range(3)]
    assert log.since("b1-1") == (frames[1:], False)
    assert log.since("b1-3") == ([], False)


def test_a_gap_when_the_replay_cannot_cover_it() -> None:
    log = EventLog(keep=2, boot="b1")
    frames = [log.record(f'{{"n": {n}}}') for n in range(4)]
    assert log.since("b1-1") == (frames[2:], True)  # event 2 fell out of the buffer
    assert log.since("b1-2") == (frames[2:], False)
    assert log.since("other-3") == ([], True)  # the bridge restarted since
    assert log.since(None) == ([], True)
    assert log.since("garbage") == ([], True)


def test_hello_frame() -> None:
    log = EventLog(keep=2, boot="b1")
    log.record("{}")
    event, data = log.hello(gap=True).split("\n", 1)
    assert event == "event: hello"
    assert json.loads(data.removeprefix("data: ")) == {"boot": "b1", "seq": 1, "gap": True}
