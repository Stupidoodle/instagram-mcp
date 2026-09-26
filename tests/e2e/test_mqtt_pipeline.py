"""E2E tests for the raw MQTT pipeline — connection and event delivery.

Tests the MQTTManager and MQTToTConnection against live Instagram MQTT
servers. Verifies that messages sent by bot2 are received
as real MessageEvent objects by bot1's MQTT reader thread.
"""

from __future__ import annotations

import queue
import time
import uuid

import pytest

from instagram_mcp.mqtt.events import MessageEvent
from tests.e2e.conftest import tap_events

pytestmark = pytest.mark.e2e


class TestMQTTConnection:
    """Verify MQTT connects and stays alive."""

    def test_mqtt_connects_and_reader_alive(self, mqtt_manager):
        """MQTTManager connects to Instagram MQTT and reader thread runs."""
        assert mqtt_manager.is_connected
        assert mqtt_manager._reader_thread is not None
        assert mqtt_manager._reader_thread.is_alive()

class TestMessageDelivery:
    """Verify real messages flow through MQTT → listener → queue."""

    def test_message_event_from_other_user(
        self,
        mqtt_manager,
        bot2_client,
        shared_thread_id,
        bot1_user_id,
    ):
        """Bot2 sends a message → bot1's MQTT delivers a MessageEvent."""
        marker = f"e2e-recv-{uuid.uuid4().hex[:8]}"

        q = tap_events(mqtt_manager, shared_thread_id)
        try:
            # Drain any stale buffered events
            from tests.e2e.conftest import drain_queue

            drain_queue(q, timeout=1)

            # Bot2 sends a real message
            bot2_client.reply_to_thread(
                thread_id=shared_thread_id,
                text=f"mqtt delivery test {marker}",
            )

            # Wait for the event (up to 15s)
            received = []
            deadline = time.time() + 15
            while time.time() < deadline:
                try:
                    event = q.get(timeout=1)
                    if isinstance(event, MessageEvent) and marker in (event.text or ""):
                        received.append(event)
                        break
                except queue.Empty:
                    continue

            assert len(received) == 1, f"Expected MessageEvent with marker {marker}"
            assert received[0].text == f"mqtt delivery test {marker}"
            assert received[0].thread_id == shared_thread_id
            # Sender is bot2, not bot1
            assert str(received[0].user_id) != bot1_user_id
        finally:
            mqtt_manager.set_listener(None)

    def test_self_echo_arrives_on_own_send(
        self,
        mqtt_manager,
        bot1_client,
        shared_thread_id,
        bot1_user_id,
    ):
        """Bot1's own message is pushed back by MQTT as a self-echo.

        This proves self-echo filtering is necessary — without it,
        wait_for_reply would treat our own messages as replies.
        """
        marker = f"e2e-echo-{uuid.uuid4().hex[:8]}"

        q = tap_events(mqtt_manager, shared_thread_id)
        try:
            from tests.e2e.conftest import drain_queue

            drain_queue(q, timeout=1)

            # Bot1 sends a message (same account as MQTT listener)
            bot1_client.reply_to_thread(
                thread_id=shared_thread_id,
                text=f"self echo test {marker}",
            )

            # Look for our own message in MQTT events
            self_echo = None
            deadline = time.time() + 15
            while time.time() < deadline:
                try:
                    event = q.get(timeout=1)
                    if (
                        isinstance(event, MessageEvent)
                        and marker in (event.text or "")
                        and str(event.user_id) == bot1_user_id
                    ):
                        self_echo = event
                        break
                except queue.Empty:
                    continue

            assert self_echo is not None, (
                f"Expected self-echo MessageEvent from bot1 (uid={bot1_user_id})"
            )
            assert self_echo.text == f"self echo test {marker}"
        finally:
            mqtt_manager.set_listener(None)

    def test_multiple_messages_in_sequence(
        self,
        mqtt_manager,
        bot2_client,
        shared_thread_id,
    ):
        """Multiple messages from bot2 are all delivered in order."""
        marker = f"e2e-seq-{uuid.uuid4().hex[:8]}"

        q = tap_events(mqtt_manager, shared_thread_id)
        try:
            from tests.e2e.conftest import drain_queue

            drain_queue(q, timeout=1)

            # Bot2 sends 3 messages rapidly
            for i in range(3):
                bot2_client.reply_to_thread(
                    thread_id=shared_thread_id,
                    text=f"seq {marker} #{i}",
                )
                time.sleep(0.5)

            # Collect all events for up to 15s
            received = []
            deadline = time.time() + 15
            while time.time() < deadline:
                try:
                    event = q.get(timeout=1)
                    if isinstance(event, MessageEvent) and marker in (event.text or ""):
                        received.append(event)
                        if len(received) == 3:
                            break
                except queue.Empty:
                    continue

            assert len(received) == 3, (
                f"Expected 3 MessageEvents with marker {marker}, got {len(received)}"
            )
            # Verify order
            for i, event in enumerate(received):
                assert event.text == f"seq {marker} #{i}"
        finally:
            mqtt_manager.set_listener(None)
