"""Unit tests for the thin client's BridgeClient and event (de)serialization."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx2
import pytest

from instagram_mcp.bridge import event_to_dict
from instagram_mcp.bridge_client import BridgeClient, BridgeError, event_from_dict
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

ROUND_TRIP_EVENTS: list[Event] = [
    MessageEvent("t1", "i1", 5, "hey", "text", 1_700_000_000_000, edited=False),
    MessageEvent("t1", "i2", 9, None, "raven_media", 1_700_000_000_001, edited=True),
    ReactionEvent("t1", "i1", 5, "emojis", "🔥"),
    ReactionEvent("t1", "i1", 5, "likes", None),
    SeenEvent("t1", 5, "i1", 42),
    TypingEvent("t1", 5, 1, 10000),
    UnsendEvent("t1", "i1", 5),
    ThreadEvent("t1", "add", "/direct_v2/inbox/threads/t1"),
]


class TestEventFromDict:
    @pytest.mark.parametrize("event", ROUND_TRIP_EVENTS)
    def test_round_trip_through_bridge_dict(self, event: Event) -> None:
        payload = event_to_dict(event)
        assert payload is not None
        assert event_from_dict(payload) == event

    def test_unknown_type_returns_none(self) -> None:
        assert event_from_dict({"type": "mystery"}) is None

    def test_malformed_payload_returns_none(self) -> None:
        # Missing the required thread_id.
        assert event_from_dict({"type": "message", "item_id": "i1"}) is None


def _client(handler: Callable[[httpx2.Request], httpx2.Response]) -> BridgeClient:
    """A BridgeClient whose transport is driven by a mock request handler."""
    bridge = BridgeClient("http://bridge.test")
    bridge._http = httpx2.Client(
        base_url="http://bridge.test", transport=httpx2.MockTransport(handler)
    )
    return bridge


class TestBridgeClient:
    def test_threads_get(self) -> None:
        seen: dict[str, httpx2.Request] = {}

        def handler(request: httpx2.Request) -> httpx2.Response:
            seen["req"] = request
            return httpx2.Response(200, json={"threads": [{"thread_id": "1"}]})

        result = _client(handler).threads(amount=5)

        assert result == [{"thread_id": "1"}]
        assert seen["req"].url.path == "/threads"
        assert seen["req"].url.params["amount"] == "5"

    def test_thread_get_returns_full_dict(self) -> None:
        def handler(_request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(200, json={"thread_id": "1", "messages": []})

        assert _client(handler).thread("1", amount=3) == {"thread_id": "1", "messages": []}

    def test_messages_and_pending_and_search_unwrap(self) -> None:
        def handler(request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(200, json={"threads": [{"thread_id": "1"}], "messages": ["m"]})

        bridge = _client(handler)
        assert bridge.messages("1") == ["m"]
        assert bridge.pending() == [{"thread_id": "1"}]
        assert bridge.search("q") == [{"thread_id": "1"}]

    def test_send_post_body(self) -> None:
        seen: dict[str, httpx2.Request] = {}

        def handler(request: httpx2.Request) -> httpx2.Response:
            seen["req"] = request
            return httpx2.Response(200, json={"success": True, "message_id": "m1"})

        result = _client(handler).send("t1", "hello")

        assert result == {"success": True, "message_id": "m1"}
        req = seen["req"]
        assert req.method == "POST"
        assert req.url.path == "/send"
        assert json.loads(req.content) == {"thread_id": "t1", "text": "hello"}

    def test_react_post_body(self) -> None:
        seen: dict[str, httpx2.Request] = {}

        def handler(request: httpx2.Request) -> httpx2.Response:
            seen["req"] = request
            return httpx2.Response(200, json={"success": True})

        _client(handler).react("t1", "m1", "🔥", remove=True)

        assert json.loads(seen["req"].content) == {
            "thread_id": "t1",
            "message_id": "m1",
            "emoji": "🔥",
            "remove": True,
        }

    def test_health_and_self_user_id(self) -> None:
        def handler(_request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(200, json={"self_user_id": "42", "mqtt_connected": True})

        bridge = _client(handler)
        assert bridge.health()["mqtt_connected"] is True
        assert bridge.self_user_id() == "42"

    def test_error_status_without_error_field(self) -> None:
        def handler(_request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(500, json={})

        assert _client(handler).send("t1", "x") == {"success": False, "error": "HTTP 500"}

    def test_post_raises_bridge_error_on_transport_failure(self) -> None:
        def handler(request: httpx2.Request) -> httpx2.Response:
            raise httpx2.ConnectError("no route", request=request)

        with pytest.raises(BridgeError):
            _client(handler).send("t1", "x")

    def test_self_user_id_empty_when_unreachable(self) -> None:
        def handler(request: httpx2.Request) -> httpx2.Response:
            raise httpx2.ConnectError("no route", request=request)

        assert _client(handler).self_user_id() == ""
