"""The seen log keeps live read receipts so history knows when a message was seen."""

from __future__ import annotations

from typing import TYPE_CHECKING

from instagram_mcp.mqtt.events import SeenEvent
from instagram_mcp.seen_log import SeenLog

if TYPE_CHECKING:
    from pathlib import Path


def test_events_come_back_per_thread_from_a_time_on(tmp_path: Path) -> None:
    log = SeenLog(tmp_path / "state" / "seen.db")
    log.add(SeenEvent("t1", 42, "i1", 1_000), now=100.0)
    log.add(SeenEvent("t1", 42, "i2", 2_000), now=200.0)
    log.add(SeenEvent("t2", 42, "i9", 3_000), now=300.0)
    assert log.since("t1", 150.0) == [{"user_id": "42", "item_id": "i2", "seen_at": 200.0}]
    log.close()


def test_the_log_survives_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "seen.db"
    first = SeenLog(path)
    first.add(SeenEvent("t1", 42, "i1", 1_000), now=100.0)
    first.close()
    assert SeenLog(path).since("t1", 0) == [{"user_id": "42", "item_id": "i1", "seen_at": 100.0}]
