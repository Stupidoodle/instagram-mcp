"""E2E tests for _send_and_check_mqtt — interjection detection.

Tests the actual function against live MQTT, verifying it correctly
detects interjections from other users, filters self-echoes, and
handles quiet threads. No mocking — real Instagram accounts, real MQTT.
"""

from __future__ import annotations

import queue
import time
import uuid

import pytest

from instagram_mcp.mqtt.events import MessageEvent
from instagram_mcp.tools.messages import _send_and_check_mqtt

pytestmark = pytest.mark.e2e


class TestSendAndCheckInterjection:
    """Verify _send_and_check_mqtt detects real interjections."""

    def test_no_interjection_quiet_thread(
        self,
        mqtt_manager,
        shared_thread_id,
        bot1_user_id,
    ):
        """No interjection when nobody sends during the check window."""
        q = mqtt_manager.router.subscribe(shared_thread_id)

        # Drain any stale events from replay buffer
        from tests.e2e.conftest import drain_queue

        drain_queue(q, timeout=1)

        # Re-subscribe fresh (drain consumed the old queue's events,
        # but _send_and_check_mqtt will unsubscribe for us)
        mqtt_manager.router.unsubscribe(shared_thread_id, q)
        q = mqtt_manager.router.subscribe(shared_thread_id)

        # Nobody sends anything — should return no interjection
        result = _send_and_check_mqtt(
            mqtt_manager,
            q,
            shared_thread_id,
            self_user_id=bot1_user_id,
        )

        assert result["success"] is True
        assert result["has_interjection"] is False
        assert "interjection" not in result

    def test_interjection_from_other(
        self,
        mqtt_manager,
        bot2_client,
        shared_thread_id,
        bot1_user_id,
    ):
        """Bot2 sends during the check window → detected as interjection."""
        marker = f"e2e-interj-{uuid.uuid4().hex[:8]}"

        # Subscribe (captures events from now on)
        q = mqtt_manager.router.subscribe(shared_thread_id)
        from tests.e2e.conftest import drain_queue

        drain_queue(q, timeout=1)
        mqtt_manager.router.unsubscribe(shared_thread_id, q)
        q = mqtt_manager.router.subscribe(shared_thread_id)

        # Bot2 sends a message (this simulates an interjection)
        bot2_client.reply_to_thread(
            thread_id=shared_thread_id,
            text=f"interjection {marker}",
        )

        # Give MQTT time to deliver the event into our queue
        time.sleep(3)

        # Now check — should find the interjection already in the queue
        result = _send_and_check_mqtt(
            mqtt_manager,
            q,
            shared_thread_id,
            self_user_id=bot1_user_id,
        )

        assert result["success"] is True
        assert result["has_interjection"] is True
        assert marker in result["interjection"]["text"]

    def test_self_echo_not_interjection(
        self,
        mqtt_manager,
        bot1_client,
        shared_thread_id,
        bot1_user_id,
    ):
        """Bot1's self-echo is filtered — not treated as interjection."""
        marker = f"e2e-selfinterj-{uuid.uuid4().hex[:8]}"

        # Subscribe and drain stale events
        q = mqtt_manager.router.subscribe(shared_thread_id)
        from tests.e2e.conftest import drain_queue

        drain_queue(q, timeout=1)
        mqtt_manager.router.unsubscribe(shared_thread_id, q)
        q = mqtt_manager.router.subscribe(shared_thread_id)

        # Bot1 sends a message (same user as MQTT listener)
        bot1_client.reply_to_thread(
            thread_id=shared_thread_id,
            text=f"self check {marker}",
        )

        # Wait for self-echo to arrive in queue
        time.sleep(3)

        # Check — self-echo should be filtered by self_user_id
        result = _send_and_check_mqtt(
            mqtt_manager,
            q,
            shared_thread_id,
            self_user_id=bot1_user_id,
        )

        assert result["success"] is True
        assert result["has_interjection"] is False

    def test_interjection_with_username_resolution(
        self,
        mqtt_manager,
        bot2_client,
        shared_thread_id,
        bot1_user_id,
        bot2_user_id,
        bot2_username,
    ):
        """Interjection sender is resolved to username via user_map."""
        marker = f"e2e-uinterj-{uuid.uuid4().hex[:8]}"
        user_map = {bot2_user_id: bot2_username}

        q = mqtt_manager.router.subscribe(shared_thread_id)
        from tests.e2e.conftest import drain_queue

        drain_queue(q, timeout=1)
        mqtt_manager.router.unsubscribe(shared_thread_id, q)
        q = mqtt_manager.router.subscribe(shared_thread_id)

        bot2_client.reply_to_thread(
            thread_id=shared_thread_id,
            text=f"named interjection {marker}",
        )
        time.sleep(3)

        result = _send_and_check_mqtt(
            mqtt_manager,
            q,
            shared_thread_id,
            self_user_id=bot1_user_id,
            user_map=user_map,
        )

        assert result["has_interjection"] is True
        assert result["interjection"]["sender"] == bot2_username
