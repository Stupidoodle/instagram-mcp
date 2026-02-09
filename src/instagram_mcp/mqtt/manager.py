"""MQTT Manager — singleton lifecycle for the persistent MQTT connection.

Owns the connection, reader thread, and event router. Tool calls delegate
to this class for push-based message reception.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from typing import TYPE_CHECKING

from instagram_mcp.mqtt.connection import PINGREQ, PINGRESP, PUBACK, PUBLISH, MQTToTConnection
from instagram_mcp.mqtt.events import Event, MessageEvent
from instagram_mcp.mqtt.parser import parse_payload, parse_publish_packet
from instagram_mcp.mqtt.router import EventRouter
from instagram_mcp.mqtt.thrift import build_connect_payload
from instagram_mcp.mqtt.topics import SUB_IRIS

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger("instagram_mcp.mqtt")

# Keepalive interval (must be < server's keepalive timeout, typically 60s)
_KEEPALIVE_INTERVAL = 55


class MQTTManager:
    """Manages the persistent MQTT connection and event routing.

    Thread-safe. The reader loop runs in a daemon thread and delivers
    events via the EventRouter to per-thread subscriber queues.
    """

    def __init__(self) -> None:
        self._conn = MQTToTConnection()
        self._router = EventRouter()
        self._reader_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._packet_id_counter = 1
        self._packet_id_lock = threading.Lock()

    @property
    def is_connected(self) -> bool:
        """Check if the MQTT connection is alive and the reader thread is running."""
        return (
            self._conn.is_connected
            and not self._stop_event.is_set()
            and self._reader_thread is not None
            and self._reader_thread.is_alive()
        )

    @property
    def router(self) -> EventRouter:
        """Access the event router for direct subscription."""
        return self._router

    def connect(
        self,
        session_file: Path,
        seq_id: int,
        snapshot_at_ms: int = 0,
        app_version: str = "415.0.0.36.76",
    ) -> None:
        """Connect to Instagram MQTT and start the reader thread.

        Args:
            session_file: Path to the instagrapi JSON session file.
            seq_id: Iris sequence ID from direct_v2/inbox/ REST API.
            snapshot_at_ms: Snapshot timestamp from direct_v2/inbox/.
            app_version: Instagram app version for the subscription.

        Raises:
            FileNotFoundError: If session file doesn't exist.
            RuntimeError: If MQTT connection fails.
        """
        session = json.loads(session_file.read_text())
        payload = build_connect_payload(session)

        self._conn.connect(payload, keepalive=60)
        self._conn.set_timeout(1.0)

        # Subscribe to Iris for DM events — must include all fields
        self._publish(
            SUB_IRIS,
            {
                "seq_id": seq_id,
                "snapshot_at_ms": snapshot_at_ms,
                "snapshot_app_version": app_version,
                "subscription_type": "message",
            },
        )
        logger.info("Subscribed to Iris (seq_id=%d, snapshot_at_ms=%d)", seq_id, snapshot_at_ms)

        # Start background reader
        self._stop_event.clear()
        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            name="mqtt-reader",
            daemon=True,
        )
        self._reader_thread.start()

    def disconnect(self) -> None:
        """Stop the reader thread and disconnect."""
        self._stop_event.set()
        self._conn.disconnect()
        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=5)
        self._reader_thread = None

    def wait_for_message(
        self,
        thread_id: str,
        timeout: float = 300,
    ) -> MessageEvent | None:
        """Wait for a new message in a specific thread.

        Args:
            thread_id: The Instagram thread ID to listen for.
            timeout: Maximum seconds to wait.

        Returns:
            The first MessageEvent received, or None on timeout.
        """
        q = self._router.subscribe(thread_id)
        try:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    event = q.get(timeout=min(remaining, 1.0))
                    if isinstance(event, MessageEvent):
                        return event
                except queue.Empty:
                    if not self.is_connected:
                        return None
        finally:
            self._router.unsubscribe(thread_id, q)
        return None

    def collect_events(
        self,
        thread_id: str,
        window: float = 10.0,
    ) -> list[Event]:
        """Collect all events for a thread within a time window.

        Used for grace periods (catching double-texts after first reply).

        Args:
            thread_id: The Instagram thread ID.
            window: Seconds to keep collecting events.

        Returns:
            List of events received during the window.
        """
        q = self._router.subscribe(thread_id)
        events: list[Event] = []
        try:
            deadline = time.monotonic() + window
            while time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    event = q.get(timeout=min(remaining, 0.5))
                    events.append(event)
                except queue.Empty:
                    pass
        finally:
            self._router.unsubscribe(thread_id, q)
        return events

    def _reader_loop(self) -> None:
        """Background thread: read MQTT packets, parse, route events."""
        last_ping = time.monotonic()
        logger.info("MQTT reader loop started")

        try:
            while not self._stop_event.is_set():
                # Keepalive
                now = time.monotonic()
                if now - last_ping >= _KEEPALIVE_INTERVAL:
                    self._conn.send_pingreq()
                    last_ping = now

                # Read one packet
                result = self._conn.read_packet()
                if result is None:
                    if not self._conn.is_connected:
                        logger.warning("MQTT connection lost")
                        break
                    continue

                ptype, first_byte, body = result

                if ptype == PUBLISH:
                    try:
                        self._handle_publish(first_byte, body)
                    except Exception:
                        logger.warning(
                            "Failed to handle PUBLISH (topic byte %d, %d bytes)",
                            first_byte,
                            len(body),
                            exc_info=True,
                        )
                elif ptype == PINGREQ:
                    self._conn.send_pingresp()
                elif ptype == PUBACK:
                    logger.debug("PUBACK received (packet_id in body)")
                elif ptype == PINGRESP:
                    logger.debug("PINGRESP received")
                else:
                    logger.info("MQTT packet type=%d (%dB)", ptype, len(body))
        except Exception:
            logger.exception("MQTT reader loop crashed")

        logger.info("MQTT reader loop stopped")

    def _handle_publish(self, first_byte: int, body: bytes) -> None:
        """Parse a PUBLISH packet and deliver events to subscribers."""
        topic, packet_id, payload = parse_publish_packet(first_byte, body)
        logger.info("MQTT PUBLISH received: topic=%s (%dB)", topic, len(payload))

        if packet_id is not None:
            self._conn.send_puback(packet_id)

        events = parse_payload(topic, payload)
        for event in events:
            subs = self._router.active_threads
            delivered = self._router.deliver(event)
            if delivered > 0:
                logger.info(
                    "MQTT event: %s → thread %s (%d subscriber(s))",
                    type(event).__name__,
                    event.thread_id,
                    delivered,
                )
            else:
                logger.info(
                    "MQTT event: %s → thread %s (dropped, no subscribers; active=%s)",
                    type(event).__name__,
                    event.thread_id,
                    subs,
                )

    def _publish(self, topic_id: int, payload: dict) -> None:
        """Publish to an MQTT topic with auto-incrementing packet ID."""
        with self._packet_id_lock:
            pid = self._packet_id_counter
            self._packet_id_counter = (self._packet_id_counter % 65535) + 1
        self._conn.publish(topic_id, payload, qos=1, packet_id=pid)
