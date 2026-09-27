"""MQTT Manager — singleton lifecycle for the persistent MQTT connection.

Owns the connection, the reader thread and a watchdog that reconnects a dead
connection. Every parsed event goes to one listener (the Claude Code channel),
which pushes it into the live session; nothing waits or polls for replies.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from typing import TYPE_CHECKING

from instagram_mcp.mqtt.connection import PINGREQ, PINGRESP, PUBACK, PUBLISH, MQTToTConnection
from instagram_mcp.mqtt.parser import parse_payload, parse_publish_packet
from instagram_mcp.mqtt.thrift import build_connect_payload
from instagram_mcp.mqtt.topics import SEND_MESSAGE, SUB_IRIS

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from instagram_mcp.mqtt.events import Event

logger = logging.getLogger("instagram_mcp.mqtt")

# Keepalive interval (must be < server's keepalive timeout, typically 60s)
_KEEPALIVE_INTERVAL = 55

# After sending a PINGREQ, if no packet arrives within this window, connection is dead.
# Much faster than waiting for a blanket "no packets for N seconds" timeout.
_PINGREQ_RESPONSE_TIMEOUT = 15

# Fallback: if no packet received at all for this long, consider connection dead.
# Only matters if we somehow never send a PINGREQ (shouldn't happen).
_STALE_TIMEOUT = 90

# How often the watchdog checks the connection and reconnects a dead one.
_WATCHDOG_INTERVAL = 10


class MQTTManager:
    """Manages the persistent MQTT connection and event delivery.

    Thread-safe. The reader loop runs in a daemon thread and hands every
    event to the listener set with ``set_listener()``. Connection parameters
    are stored so ``ensure_connected()`` (called by the watchdog) can
    transparently recover from dead connections.
    """

    def __init__(self) -> None:
        self._conn = MQTToTConnection()
        self._listener: Callable[[Event], None] | None = None
        self._watchdog_thread: threading.Thread | None = None
        self._watchdog_stop = threading.Event()
        self._reader_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._packet_id_counter = 1
        self._packet_id_lock = threading.Lock()
        self._last_packet_time: float = 0.0  # monotonic timestamp of last received packet
        self._pingreq_sent_at: float = 0.0  # monotonic time of last PINGREQ we sent
        self._reconnect_lock = threading.Lock()
        self._reconnect_count: int = 0

        # Stored connection params for auto-reconnect
        self._session_file: Path | None = None
        self._seq_id: int = 0
        self._snapshot_at_ms: int = 0
        self._app_version: str = "415.0.0.36.76"

    @property
    def is_connected(self) -> bool:
        """Check if the MQTT connection is alive and the reader thread is running.

        Uses two-tier stale detection:
        1. PINGREQ timeout: if we sent a PINGREQ and got nothing back in
           _PINGREQ_RESPONSE_TIMEOUT seconds, the connection is dead (~15s).
        2. Blanket timeout: if no packet at all for _STALE_TIMEOUT seconds (~90s).
        """
        if not self._conn.is_connected:
            return False
        if self._stop_event.is_set():
            return False
        if self._reader_thread is None or not self._reader_thread.is_alive():
            return False

        now = time.monotonic()

        # Tier 1: PINGREQ sent but no response
        if self._pingreq_sent_at > 0 and self._pingreq_sent_at > self._last_packet_time:
            # We sent a PINGREQ more recently than we received any packet
            since_ping = now - self._pingreq_sent_at
            if since_ping > _PINGREQ_RESPONSE_TIMEOUT:
                logger.warning(
                    "MQTT stale: PINGREQ sent %.0fs ago with no response",
                    since_ping,
                )
                return False

        # Tier 2: blanket silence
        if self._last_packet_time > 0:
            silence = now - self._last_packet_time
            if silence > _STALE_TIMEOUT:
                logger.warning("MQTT stale: no packets for %.0fs", silence)
                return False

        return True

    @property
    def reconnect_count(self) -> int:
        """Number of auto-reconnections since initial connect."""
        return self._reconnect_count

    def set_listener(self, listener: Callable[[Event], None] | None) -> None:
        """Set the callback that receives every parsed event, on the reader thread.

        It must not block.
        """
        self._listener = listener

    def start_watchdog(self, interval: float = _WATCHDOG_INTERVAL) -> None:
        """Start a daemon thread that reconnects the connection when it dies.

        Without it a dead connection only recovers when a tool calls
        ``ensure_connected()``, and pushed events would silently stop.
        """
        if self._watchdog_thread and self._watchdog_thread.is_alive():
            return
        self._watchdog_stop.clear()
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop,
            args=(interval,),
            name="mqtt-watchdog",
            daemon=True,
        )
        self._watchdog_thread.start()

    def indicate_activity(self, thread_id: str, *, active: bool = True) -> None:
        """Show or clear the typing indicator in a thread.

        Sends the ``indicate_activity`` command on ``/ig_send_message``, the
        same one the Instagram app sends while you type.

        Raises:
            RuntimeError: If the MQTT connection is down and can't be restored.
        """
        if not self.ensure_connected():
            msg = "MQTT not connected"
            raise RuntimeError(msg)
        self._publish(
            SEND_MESSAGE,
            {
                "action": "indicate_activity",
                "thread_id": thread_id,
                "client_context": str(uuid.uuid4()),
                "activity_status": "1" if active else "0",
            },
        )

    def _watchdog_loop(self, interval: float) -> None:
        """Reconnect whenever the connection goes stale, until stopped."""
        while not self._watchdog_stop.wait(interval):
            try:
                self.ensure_connected()
            except Exception:
                logger.exception("MQTT watchdog reconnect failed")

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
        # Store params for auto-reconnect
        self._session_file = session_file
        self._seq_id = seq_id
        self._snapshot_at_ms = snapshot_at_ms
        self._app_version = app_version

        self._do_connect(session_file, seq_id, snapshot_at_ms, app_version)

    def _do_connect(
        self,
        session_file: Path,
        seq_id: int,
        snapshot_at_ms: int,
        app_version: str,
    ) -> None:
        """Internal connect — used by both connect() and reconnect()."""
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

    def ensure_connected(self) -> bool:
        """Check connection health and auto-reconnect if stale/dead.

        Returns:
            True if connected (possibly after reconnect), False if reconnect failed.
        """
        if self.is_connected:
            return True

        if self._session_file is None:
            logger.warning("Cannot reconnect: no stored connection params")
            return False

        with self._reconnect_lock:
            # Double-check after acquiring lock
            if self.is_connected:
                return True

            logger.warning("MQTT connection dead, attempting reconnect...")

            # Tear down old connection
            self._stop_event.set()
            self._conn.disconnect()
            if self._reader_thread and self._reader_thread.is_alive():
                self._reader_thread.join(timeout=3)
            self._reader_thread = None

            try:
                self._do_connect(
                    self._session_file,
                    self._seq_id,
                    self._snapshot_at_ms,
                    self._app_version,
                )
                # Wait briefly for reader to stabilize
                time.sleep(1)
                self._reconnect_count += 1
                logger.info(
                    "MQTT reconnected successfully (reconnect #%d)",
                    self._reconnect_count,
                )
                return True
            except Exception:
                logger.exception("MQTT reconnect failed")
                return False

    def disconnect(self) -> None:
        """Stop the watchdog and reader threads and disconnect."""
        self._watchdog_stop.set()
        self._stop_event.set()
        self._conn.disconnect()
        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=5)
        self._reader_thread = None

    def _reader_loop(self) -> None:  # noqa: PLR0912
        """Background thread: read MQTT packets, parse, route events."""
        last_ping = time.monotonic()
        self._last_packet_time = time.monotonic()
        self._pingreq_sent_at = 0.0
        logger.info("MQTT reader loop started")

        try:
            while not self._stop_event.is_set():
                now = time.monotonic()

                # Keepalive
                if now - last_ping >= _KEEPALIVE_INTERVAL:
                    self._conn.send_pingreq()
                    self._pingreq_sent_at = now
                    last_ping = now
                    logger.debug(
                        "Sent PINGREQ (last packet %.0fs ago)",
                        now - self._last_packet_time,
                    )

                # Read one packet (blocks up to socket timeout, typically 1s)
                result = self._conn.read_packet()
                if result is None:
                    # Socket returned None — either timeout or connection closed
                    if not self._conn.is_connected:
                        logger.warning("MQTT connection lost (socket closed by remote)")
                        break

                    # Check PINGREQ-based stale detection (fast path, ~15s)
                    if self._pingreq_sent_at > 0 and self._pingreq_sent_at > self._last_packet_time:
                        since_ping = now - self._pingreq_sent_at
                        if since_ping > _PINGREQ_RESPONSE_TIMEOUT:
                            logger.warning(
                                "MQTT stale: sent PINGREQ %.0fs ago, no response. "
                                "Last packet %.0fs ago. Closing.",
                                since_ping,
                                now - self._last_packet_time,
                            )
                            self._conn.disconnect()
                            break

                    # Blanket stale detection (fallback, ~90s)
                    if now - self._last_packet_time > _STALE_TIMEOUT:
                        logger.warning(
                            "MQTT stale: no packets for %.0fs, closing",
                            now - self._last_packet_time,
                        )
                        self._conn.disconnect()
                        break
                    continue

                # Got a packet — connection is alive
                self._last_packet_time = time.monotonic()
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
                    logger.debug(
                        "PINGRESP received (%.0fs after PINGREQ)",
                        time.monotonic() - self._pingreq_sent_at
                        if self._pingreq_sent_at > 0
                        else 0,
                    )
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

        events, seq_id = parse_payload(topic, payload)

        # Advance the Iris seq_id cursor so reconnections don't replay old events
        if seq_id > self._seq_id:
            logger.debug("Iris seq_id advanced: %d → %d", self._seq_id, seq_id)
            self._seq_id = seq_id

        listener = self._listener
        for event in events:
            logger.debug("MQTT event: %s → thread %s", type(event).__name__, event.thread_id)
            if listener is None:
                continue
            try:
                listener(event)
            except Exception:
                logger.exception("MQTT listener failed on %s", type(event).__name__)

    def _publish(self, topic_id: int, payload: dict) -> None:
        """Publish to an MQTT topic with auto-incrementing packet ID."""
        with self._packet_id_lock:
            pid = self._packet_id_counter
            self._packet_id_counter = (self._packet_id_counter % 65535) + 1
        self._conn.publish(topic_id, payload, qos=1, packet_id=pid)
