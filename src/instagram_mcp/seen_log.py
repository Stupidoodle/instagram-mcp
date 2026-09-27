"""Read receipts as they arrive, kept so history can tell how fast a message was seen.

Instagram's API only remembers the latest point each person has seen, so per-message
read times exist only if the live seen events are recorded.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from instagram_mcp.mqtt.events import SeenEvent

_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
    thread_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    seen_at REAL NOT NULL,
    raw_timestamp INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS seen_thread_time ON seen (thread_id, seen_at);
"""


class SeenLog:
    """A small SQLite log of seen events (who saw up to which message, when)."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(_SCHEMA)

    def add(self, event: SeenEvent, now: float | None = None) -> None:
        """Record a seen event at the time it arrived."""
        row = (
            event.thread_id,
            str(event.user_id),
            event.item_id,
            time.time() if now is None else now,
            event.timestamp,
        )
        with self._lock, self._db:
            self._db.execute("INSERT INTO seen VALUES (?, ?, ?, ?, ?)", row)

    def since(self, thread_id: str, since: float) -> list[dict[str, Any]]:
        """Seen events of a thread from `since` (epoch seconds) on, oldest first."""
        with self._lock:
            rows = self._db.execute(
                "SELECT user_id, item_id, seen_at FROM seen"
                " WHERE thread_id = ? AND seen_at >= ? ORDER BY seen_at",
                (thread_id, since),
            ).fetchall()
        return [{"user_id": u, "item_id": i, "seen_at": t} for u, i, t in rows]

    def close(self) -> None:
        """Close the database."""
        with self._lock:
            self._db.close()
