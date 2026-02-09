"""Unit tests for MQTT event parser."""

import json
import zlib

from instagram_mcp.mqtt.events import (
    MessageEvent,
    ReactionEvent,
    SeenEvent,
    ThreadEvent,
    TypingEvent,
    UnsendEvent,
)
from instagram_mcp.mqtt.parser import (
    parse_payload,
    parse_publish_packet,
)


class TestParsePublishPacket:
    def test_qos0(self) -> None:
        # Topic "146" (3 bytes), no packet ID, payload "test"
        import struct

        topic = b"146"
        payload = b"test"
        body = struct.pack("!H", len(topic)) + topic + payload
        first_byte = 0x30  # PUBLISH, QoS 0

        t, pid, p = parse_publish_packet(first_byte, body)
        assert t == "146"
        assert pid is None
        assert p == b"test"

    def test_qos1(self) -> None:
        import struct

        topic = b"146"
        packet_id = 42
        payload = b"data"
        body = struct.pack("!H", len(topic)) + topic + struct.pack("!H", packet_id) + payload
        first_byte = 0x32  # PUBLISH, QoS 1

        t, pid, p = parse_publish_packet(first_byte, body)
        assert t == "146"
        assert pid == 42
        assert p == b"data"


class TestParseIrisMessageEvent:
    def _make_payload(self, patches: list[dict]) -> bytes:
        """Create a zlib-compressed topic 146 payload."""
        data = [{"event": "patch", "data": patches, "seq_id": 1, "realtime": True}]
        return zlib.compress(json.dumps(data).encode())

    def test_new_text_message(self) -> None:
        value = json.dumps(
            {
                "item_id": "123456",
                "user_id": 99999,
                "timestamp": "1770628773364437",
                "item_type": "text",
                "text": "hello world",
            }
        )
        payload = self._make_payload(
            [
                {
                    "op": "add",
                    "path": "/direct_v2/threads/THREAD123/items/123456",
                    "value": value,
                }
            ]
        )

        events = parse_payload("146", payload)
        assert len(events) == 1
        assert isinstance(events[0], MessageEvent)
        msg = events[0]
        assert msg.thread_id == "THREAD123"
        assert msg.item_id == "123456"
        assert msg.user_id == 99999
        assert msg.text == "hello world"
        assert msg.item_type == "text"
        assert msg.timestamp == 1770628773364437

    def test_media_message(self) -> None:
        value = json.dumps(
            {
                "item_id": "789",
                "user_id": 11111,
                "timestamp": "1770628773000000",
                "item_type": "media",
                "text": None,
            }
        )
        payload = self._make_payload(
            [
                {
                    "op": "add",
                    "path": "/direct_v2/threads/T1/items/789",
                    "value": value,
                }
            ]
        )

        events = parse_payload("146", payload)
        assert len(events) == 1
        assert isinstance(events[0], MessageEvent)
        assert events[0].item_type == "media"
        assert events[0].text is None

    def test_multiple_patches_in_one_payload(self) -> None:
        patches = [
            {
                "op": "add",
                "path": "/direct_v2/threads/T1/items/A",
                "value": json.dumps(
                    {
                        "item_id": "A",
                        "user_id": 1,
                        "timestamp": "0",
                        "item_type": "text",
                        "text": "first",
                    }
                ),
            },
            {
                "op": "add",
                "path": "/direct_v2/threads/T1/items/B",
                "value": json.dumps(
                    {
                        "item_id": "B",
                        "user_id": 2,
                        "timestamp": "0",
                        "item_type": "text",
                        "text": "second",
                    }
                ),
            },
        ]
        payload = self._make_payload(patches)

        events = parse_payload("146", payload)
        assert len(events) == 2
        assert all(isinstance(e, MessageEvent) for e in events)
        assert events[0].text == "first"
        assert events[1].text == "second"


class TestParseIrisUnsendEvent:
    def test_message_unsend(self) -> None:
        data = [
            {
                "event": "patch",
                "data": [
                    {
                        "op": "remove",
                        "path": "/direct_v2/threads/T1/items/ITEM123",
                        "value": json.dumps({"user_id": 55555}),
                    }
                ],
                "seq_id": 2,
            }
        ]
        payload = zlib.compress(json.dumps(data).encode())

        events = parse_payload("146", payload)
        assert len(events) == 1
        assert isinstance(events[0], UnsendEvent)
        assert events[0].thread_id == "T1"
        assert events[0].item_id == "ITEM123"
        assert events[0].user_id == 55555

    def test_unsend_no_user_id(self) -> None:
        """Unsend with empty value still works."""
        data = [
            {
                "event": "patch",
                "data": [
                    {
                        "op": "remove",
                        "path": "/direct_v2/threads/T2/items/X",
                        "value": "{}",
                    }
                ],
                "seq_id": 3,
            }
        ]
        payload = zlib.compress(json.dumps(data).encode())

        events = parse_payload("146", payload)
        assert len(events) == 1
        assert isinstance(events[0], UnsendEvent)
        assert events[0].user_id == 0


class TestParseIrisSeenEvent:
    def test_read_receipt(self) -> None:
        data = [
            {
                "event": "patch",
                "data": [
                    {
                        "op": "replace",
                        "path": "/direct_v2/threads/T1/participants/99999/has_seen",
                        "value": json.dumps(
                            {"item_id": "ITEM456", "timestamp": "1770600000000000"}
                        ),
                    }
                ],
                "seq_id": 4,
            }
        ]
        payload = zlib.compress(json.dumps(data).encode())

        events = parse_payload("146", payload)
        assert len(events) == 1
        assert isinstance(events[0], SeenEvent)
        assert events[0].thread_id == "T1"
        assert events[0].user_id == 99999
        assert events[0].item_id == "ITEM456"
        assert events[0].timestamp == 1770600000000000


class TestParseIrisReactionEvent:
    def test_reaction_add(self) -> None:
        data = [
            {
                "event": "patch",
                "data": [
                    {
                        "op": "add",
                        "path": "/direct_v2/threads/T1/items/ITEM789/reactions/emojis/88888",
                        "value": json.dumps({"emoji": "\u2764\ufe0f"}),
                    }
                ],
                "seq_id": 5,
            }
        ]
        payload = zlib.compress(json.dumps(data).encode())

        events = parse_payload("146", payload)
        assert len(events) == 1
        assert isinstance(events[0], ReactionEvent)
        assert events[0].thread_id == "T1"
        assert events[0].item_id == "ITEM789"
        assert events[0].user_id == 88888
        assert events[0].reaction_type == "emojis"
        assert events[0].emoji == "\u2764\ufe0f"

    def test_reaction_remove(self) -> None:
        data = [
            {
                "event": "patch",
                "data": [
                    {
                        "op": "remove",
                        "path": "/direct_v2/threads/T1/items/ITEM789/reactions/likes/77777",
                        "value": "{}",
                    }
                ],
                "seq_id": 6,
            }
        ]
        payload = zlib.compress(json.dumps(data).encode())

        events = parse_payload("146", payload)
        assert len(events) == 1
        assert isinstance(events[0], ReactionEvent)
        assert events[0].emoji is None
        assert events[0].reaction_type == "likes"


class TestParseIrisTypingEvent:
    def test_activity_indicator(self) -> None:
        """Typing indicator via topic 146 activity_indicator path."""
        data = [
            {
                "event": "patch",
                "data": [
                    {
                        "op": "replace",
                        "path": "/direct_v2/threads/T1/activity_indicator_id/123",
                        "value": json.dumps(
                            {
                                "sender_id": "44444",
                                "activity_status": 1,
                                "ttl": 10000,
                            }
                        ),
                    }
                ],
                "seq_id": 7,
            }
        ]
        payload = zlib.compress(json.dumps(data).encode())

        events = parse_payload("146", payload)
        assert len(events) == 1
        assert isinstance(events[0], TypingEvent)
        assert events[0].thread_id == "T1"
        assert events[0].user_id == 44444
        assert events[0].activity_status == 1
        assert events[0].ttl == 10000


class TestParseIrisThreadEvent:
    def test_inbox_thread_event(self) -> None:
        data = [
            {
                "event": "patch",
                "data": [
                    {
                        "op": "replace",
                        "path": "/direct_v2/inbox/threads/T99",
                        "value": "{}",
                    }
                ],
                "seq_id": 8,
            }
        ]
        payload = zlib.compress(json.dumps(data).encode())

        events = parse_payload("146", payload)
        assert len(events) == 1
        assert isinstance(events[0], ThreadEvent)
        assert events[0].thread_id == "T99"
        assert events[0].op == "replace"


class TestParsePubsubTyping:
    def test_typing_from_topic_88(self) -> None:
        data = {
            "path": "/direct_v2/threads/T5/activity_indicator_id/xyz",
            "value": json.dumps(
                {
                    "sender_id": "33333",
                    "activity_status": 1,
                    "ttl": 5000,
                }
            ),
        }
        payload = zlib.compress(json.dumps(data).encode())

        events = parse_payload("88", payload)
        assert len(events) == 1
        assert isinstance(events[0], TypingEvent)
        assert events[0].thread_id == "T5"
        assert events[0].user_id == 33333
        assert events[0].activity_status == 1


class TestParseEdgeCases:
    def test_unhandled_topic(self) -> None:
        payload = zlib.compress(b'{"test": true}')
        events = parse_payload("999", payload)
        assert events == []

    def test_invalid_zlib(self) -> None:
        events = parse_payload("146", b"not-zlib-data")
        assert events == []

    def test_invalid_json(self) -> None:
        payload = zlib.compress(b"not json")
        events = parse_payload("146", payload)
        assert events == []

    def test_empty_data_array(self) -> None:
        data = [{"event": "patch", "data": [], "seq_id": 0}]
        payload = zlib.compress(json.dumps(data).encode())
        events = parse_payload("146", payload)
        assert events == []

    def test_non_list_iris_payload(self) -> None:
        """Single object (not array) is wrapped to list."""
        data = {
            "event": "patch",
            "data": [
                {
                    "op": "add",
                    "path": "/direct_v2/threads/T1/items/I1",
                    "value": json.dumps(
                        {
                            "item_id": "I1",
                            "user_id": 1,
                            "timestamp": "0",
                            "item_type": "text",
                            "text": "hi",
                        }
                    ),
                }
            ],
            "seq_id": 1,
        }
        payload = zlib.compress(json.dumps(data).encode())
        events = parse_payload("146", payload)
        assert len(events) == 1
        assert isinstance(events[0], MessageEvent)

    def test_non_dict_sync_item_skipped(self) -> None:
        data = ["not_a_dict", 42]
        payload = zlib.compress(json.dumps(data).encode())
        events = parse_payload("146", payload)
        assert events == []

    def test_non_dict_patch_skipped(self) -> None:
        data = [{"event": "patch", "data": ["not_a_dict"], "seq_id": 0}]
        payload = zlib.compress(json.dumps(data).encode())
        events = parse_payload("146", payload)
        assert events == []

    def test_unrecognized_path(self) -> None:
        """Path that doesn't match any known pattern returns None."""
        data = [
            {
                "event": "patch",
                "data": [{"op": "add", "path": "/unknown/path", "value": "{}"}],
                "seq_id": 0,
            }
        ]
        payload = zlib.compress(json.dumps(data).encode())
        events = parse_payload("146", payload)
        assert events == []

    def test_short_thread_path(self) -> None:
        """Path with too few segments for thread routing."""
        data = [
            {
                "event": "patch",
                "data": [{"op": "add", "path": "/direct_v2/threads", "value": "{}"}],
                "seq_id": 0,
            }
        ]
        payload = zlib.compress(json.dumps(data).encode())
        events = parse_payload("146", payload)
        assert events == []

    def test_message_invalid_value_json(self) -> None:
        """Invalid JSON in value field returns None for that patch."""
        data = [
            {
                "event": "patch",
                "data": [
                    {
                        "op": "add",
                        "path": "/direct_v2/threads/T1/items/I1",
                        "value": "not-json{{{",
                    }
                ],
                "seq_id": 0,
            }
        ]
        payload = zlib.compress(json.dumps(data).encode())
        events = parse_payload("146", payload)
        assert events == []

    def test_message_non_dict_value(self) -> None:
        """Non-dict JSON value returns None."""
        data = [
            {
                "event": "patch",
                "data": [
                    {
                        "op": "add",
                        "path": "/direct_v2/threads/T1/items/I1",
                        "value": json.dumps([1, 2, 3]),
                    }
                ],
                "seq_id": 0,
            }
        ]
        payload = zlib.compress(json.dumps(data).encode())
        events = parse_payload("146", payload)
        assert events == []

    def test_seen_invalid_user_id(self) -> None:
        """has_seen path with non-numeric user_id."""
        data = [
            {
                "event": "patch",
                "data": [
                    {
                        "op": "replace",
                        "path": "/direct_v2/threads/T1/participants/not_a_number/has_seen",
                        "value": json.dumps({"item_id": "X", "timestamp": "0"}),
                    }
                ],
                "seq_id": 0,
            }
        ]
        payload = zlib.compress(json.dumps(data).encode())
        events = parse_payload("146", payload)
        assert events == []

    def test_reaction_short_path(self) -> None:
        """Reaction path with too few segments falls to message parse."""
        data = [
            {
                "event": "patch",
                "data": [
                    {
                        "op": "add",
                        "path": "/direct_v2/threads/T1/items/I1/reactions/emojis",
                        "value": "{}",
                    }
                ],
                "seq_id": 0,
            }
        ]
        payload = zlib.compress(json.dumps(data).encode())
        events = parse_payload("146", payload)
        # Path has items subpath + op=add but < 9 parts so reaction check fails.
        # Falls through to message add (items + len >= 6 + op=add).
        assert len(events) == 1
        assert isinstance(events[0], MessageEvent)

    def test_reaction_invalid_user_id(self) -> None:
        """Reaction path with non-numeric user_id returns no event."""
        data = [
            {
                "event": "patch",
                "data": [
                    {
                        "op": "add",
                        "path": "/direct_v2/threads/T1/items/I1/reactions/emojis/not_number",
                        "value": "{}",
                    }
                ],
                "seq_id": 0,
            }
        ]
        payload = zlib.compress(json.dumps(data).encode())
        events = parse_payload("146", payload)
        # _parse_reaction returns None (ValueError on int("not_number"))
        assert len(events) == 0

    def test_activity_indicator_invalid_value(self) -> None:
        """activity_indicator with bad value JSON."""
        data = [
            {
                "event": "patch",
                "data": [
                    {
                        "op": "replace",
                        "path": "/direct_v2/threads/T1/activity_indicator_id/123",
                        "value": "not-json",
                    }
                ],
                "seq_id": 0,
            }
        ]
        payload = zlib.compress(json.dumps(data).encode())
        events = parse_payload("146", payload)
        # Returns None from _parse_activity_indicator, no event
        assert events == []

    def test_activity_indicator_non_dict_value(self) -> None:
        data = [
            {
                "event": "patch",
                "data": [
                    {
                        "op": "replace",
                        "path": "/direct_v2/threads/T1/activity_indicator_id/123",
                        "value": json.dumps([1, 2]),
                    }
                ],
                "seq_id": 0,
            }
        ]
        payload = zlib.compress(json.dumps(data).encode())
        events = parse_payload("146", payload)
        assert events == []

    def test_generic_thread_subpath(self) -> None:
        """Unknown subpath under threads returns ThreadEvent."""
        data = [
            {
                "event": "patch",
                "data": [
                    {
                        "op": "replace",
                        "path": "/direct_v2/threads/T1/some_unknown_field",
                        "value": "{}",
                    }
                ],
                "seq_id": 0,
            }
        ]
        payload = zlib.compress(json.dumps(data).encode())
        events = parse_payload("146", payload)
        assert len(events) == 1
        assert isinstance(events[0], ThreadEvent)

    def test_non_numeric_topic(self) -> None:
        payload = zlib.compress(b"{}")
        events = parse_payload("not_a_number", payload)
        assert events == []

    def test_pubsub_non_dict_data(self) -> None:
        payload = zlib.compress(json.dumps("just a string").encode())
        events = parse_payload("88", payload)
        assert events == []

    def test_pubsub_non_dict_item(self) -> None:
        payload = zlib.compress(json.dumps([42, "not_dict"]).encode())
        events = parse_payload("88", payload)
        assert events == []

    def test_pubsub_no_sender_id(self) -> None:
        """Pubsub item without sender_id is skipped."""
        data = {"path": "/something", "value": json.dumps({"no_sender": True})}
        payload = zlib.compress(json.dumps(data).encode())
        events = parse_payload("88", payload)
        assert events == []

    def test_pubsub_invalid_value_json(self) -> None:
        """Pubsub item with invalid JSON value string is skipped."""
        data = {"path": "/something", "value": "not-json{{{"}
        payload = zlib.compress(json.dumps(data).encode())
        events = parse_payload("88", payload)
        assert events == []

    def test_pubsub_list_format(self) -> None:
        """Pubsub data as a list of items."""
        data = [
            {
                "path": "/direct_v2/threads/T1/activity_indicator_id/x",
                "value": json.dumps({"sender_id": "111", "activity_status": 2, "ttl": 3000}),
            },
        ]
        payload = zlib.compress(json.dumps(data).encode())
        events = parse_payload("88", payload)
        assert len(events) == 1
        assert isinstance(events[0], TypingEvent)
        assert events[0].activity_status == 2

    def test_seen_invalid_value_json(self) -> None:
        data = [
            {
                "event": "patch",
                "data": [
                    {
                        "op": "replace",
                        "path": "/direct_v2/threads/T1/participants/123/has_seen",
                        "value": "not-json",
                    }
                ],
                "seq_id": 0,
            }
        ]
        payload = zlib.compress(json.dumps(data).encode())
        events = parse_payload("146", payload)
        assert events == []

    def test_unsend_invalid_value_json(self) -> None:
        """Unsend with invalid value JSON still creates event with user_id=0."""
        data = [
            {
                "event": "patch",
                "data": [
                    {
                        "op": "remove",
                        "path": "/direct_v2/threads/T1/items/X",
                        "value": "not-json",
                    }
                ],
                "seq_id": 0,
            }
        ]
        payload = zlib.compress(json.dumps(data).encode())
        events = parse_payload("146", payload)
        assert len(events) == 1
        assert isinstance(events[0], UnsendEvent)
        assert events[0].user_id == 0

    def test_replace_message(self) -> None:
        """op=replace on items path also creates MessageEvent."""
        value = json.dumps(
            {
                "item_id": "I1",
                "user_id": 5,
                "timestamp": "0",
                "item_type": "text",
                "text": "edited",
            }
        )
        data = [
            {
                "event": "patch",
                "data": [
                    {
                        "op": "replace",
                        "path": "/direct_v2/threads/T1/items/I1",
                        "value": value,
                    }
                ],
                "seq_id": 0,
            }
        ]
        payload = zlib.compress(json.dumps(data).encode())
        events = parse_payload("146", payload)
        assert len(events) == 1
        assert isinstance(events[0], MessageEvent)
        assert events[0].text == "edited"

    def test_pubsub_inline_sender_id(self) -> None:
        """Pubsub data where sender_id is directly in item (no nested value)."""
        data = {
            "sender_id": "222",
            "activity_status": 0,
            "ttl": 0,
            "thread_id": "T8",
        }
        payload = zlib.compress(json.dumps(data).encode())
        events = parse_payload("88", payload)
        assert len(events) == 1
        assert isinstance(events[0], TypingEvent)
        assert events[0].thread_id == "T8"
        assert events[0].user_id == 222
