"""Numbered recent events, so a subscriber that reconnects can catch up.

Every event the bridge sends gets an SSE id ``<boot>-<seq>``: ``boot`` changes on each
bridge start and ``seq`` counts up. A reconnecting client sends the last id it saw
(``Last-Event-ID``) and gets everything after it replayed. When that can't cover the
gap (another boot, or older than what is kept), the ``hello`` frame says ``gap: true``
and the client fills in from the chat history instead.
"""

from __future__ import annotations

import json
import uuid
from collections import deque


class EventLog:
    """The last ``keep`` SSE frames, with their sequence numbers."""

    def __init__(self, keep: int, boot: str | None = None) -> None:
        self.boot = boot or uuid.uuid4().hex[:8]
        self._seq = 0
        self._frames: deque[tuple[int, str]] = deque(maxlen=keep)

    def record(self, data: str) -> str:
        """Number an event's JSON and keep its frame; returns the frame to send."""
        self._seq += 1
        frame = f"id: {self.boot}-{self._seq}\ndata: {data}\n\n"
        self._frames.append((self._seq, frame))
        return frame

    def since(self, last_event_id: str | None) -> tuple[list[str], bool]:
        """The frames after ``last_event_id``, and whether some are missing before them."""
        boot, _, seq_text = (last_event_id or "").rpartition("-")
        if boot != self.boot or not seq_text.isdigit():
            return [], True
        seq = int(seq_text)
        oldest = self._frames[0][0] if self._frames else self._seq + 1
        return [frame for s, frame in self._frames if s > seq], seq + 1 < oldest

    def hello(self, gap: bool) -> str:
        """The first frame of a stream: this boot, the latest seq, and whether a gap exists."""
        data = json.dumps({"boot": self.boot, "seq": self._seq, "gap": gap})
        return f"event: hello\ndata: {data}\n\n"
