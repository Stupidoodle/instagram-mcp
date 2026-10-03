"""Live reply latency: ``dm.bridge.reply.duration``, by side.

A reply follows the rule of the persona retro (whatsapp-mcp ``analytics``, stats/replies.py):
a message after the other side's last one, less than the conversation gap (2 h) later; its
latency is the time between the two. Reactions and edits are no messages.

Scoped to persona traffic by the send path, so no thread id is needed on the metric. Only
two things feed the tracker: a message that arrives from the other side, and a send that
went out through this bridge's API (a persona's send tool). The account's own messages that
come back over MQTT are left out: Instagram echoes the bridge's own sends, sometimes before
the send call returns, so taking them in would race. ``side=me`` is therefore a persona's
reply through the API, ``side=them`` an answer to one. A thread nothing was sent to through
the API (every chat of the owner's own) never yields a sample. The one difference from the
WhatsApp bridge: a message the owner sends from the phone into a persona's thread is not
seen here. The thread id stays in memory.

Claude Code carries no trace context from a notification to the tool call that answers it,
so a persona's reply starts a new trace. Each counted reply links its span (the
``instagram.send`` span, or the inbound message's event span) to the span of the message it
answers, records the latency on that span, and is recorded while that span is current, so
the exemplar opens the reply's trace.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from instagram_mcp import instruments

if TYPE_CHECKING:
    from collections.abc import Callable

    from opentelemetry.trace import Span, SpanContext

CONVERSATION_GAP_S = 2 * 60 * 60.0
"""The retro's conversation gap: a longer silence starts a new conversation, not a reply."""


@dataclass(frozen=True)
class Reply:
    """A counted reply: who replied, after how long, and the span of what it answers."""

    side: str
    seconds: float
    answers: SpanContext


@dataclass(frozen=True)
class _Last:
    at: float
    from_me: bool
    span: SpanContext


class Conversations:
    """Each thread's last message, for as long as a reply to it can still count."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._last: dict[str, _Last] = {}
        self._lock = threading.Lock()

    def observe(self, thread_id: str, *, from_me: bool, span: SpanContext) -> Reply | None:
        """Note a message in a thread; the reply it is, if it counts as one.

        Args:
            thread_id: the thread (kept in memory only).
            from_me: sent through the API (True) or arrived from the other side (False).
            span: the span of the send or of the inbound event.

        Returns:
            The reply, or None when the message is no reply.
        """
        with self._lock:
            now = self._clock()
            prev = self._last.get(thread_id)
            self._last[thread_id] = _Last(now, from_me, span)
            self._prune(now)
        if prev is None or prev.from_me == from_me or now - prev.at >= CONVERSATION_GAP_S:
            return None
        return Reply("me" if from_me else "them", now - prev.at, prev.span)

    def _prune(self, now: float) -> None:
        """Forget threads whose last message is past the gap: nothing can answer them now."""
        for thread_id in [t for t, m in self._last.items() if now - m.at >= CONVERSATION_GAP_S]:
            del self._last[thread_id]

    def __len__(self) -> int:
        """How many threads are held."""
        with self._lock:
            return len(self._last)


tracker = Conversations()


def note(thread_id: str, span: Span, *, from_me: bool) -> None:
    """Feed one message to the tracker while ``span`` is current; record it if it is a reply."""
    reply = tracker.observe(thread_id, from_me=from_me, span=span.get_span_context())
    if reply is None:
        return
    span.set_attributes({"dm.reply.side": reply.side, "dm.reply.seconds": reply.seconds})
    if reply.answers.is_valid:
        span.add_link(reply.answers, {"dm.link.type": "reply_to"})
    attributes = {"platform": instruments.PLATFORM, "side": reply.side}
    instruments.reply_duration.record(reply.seconds, attributes)
