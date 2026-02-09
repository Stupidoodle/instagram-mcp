"""Instagram MQTToT realtime messaging module.

Provides push-based message reception via Instagram's MQTT infrastructure,
replacing REST polling with <100ms event delivery.
"""

from instagram_mcp.mqtt.events import (
    Event,
    MessageEvent,
    ReactionEvent,
    SeenEvent,
    ThreadEvent,
    TypingEvent,
    UnsendEvent,
)
from instagram_mcp.mqtt.manager import MQTTManager
from instagram_mcp.mqtt.router import EventRouter

__all__ = [
    "Event",
    "EventRouter",
    "MQTTManager",
    "MessageEvent",
    "ReactionEvent",
    "SeenEvent",
    "ThreadEvent",
    "TypingEvent",
    "UnsendEvent",
]
