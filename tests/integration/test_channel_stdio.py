"""Wire-level test: the channel over real stdio, as Claude Code sees it."""

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from mcp_types.version import LATEST_HANDSHAKE_VERSION, LATEST_MODERN_VERSION

SERVER = Path(__file__).with_name("channel_stdio_server.py")

# 2026-07-28+ has no initialize handshake: every request carries this envelope.
MODERN_META = {
    "io.modelcontextprotocol/protocolVersion": LATEST_MODERN_VERSION,
    "io.modelcontextprotocol/clientInfo": {"name": "claude-code", "version": "test"},
    "io.modelcontextprotocol/clientCapabilities": {},
}


def _spawn(*args: str) -> subprocess.Popen[str]:
    return subprocess.Popen(  # noqa: S603 - our own server script, fixed argv
        [sys.executable, str(SERVER), *args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )


def _send(proc: subprocess.Popen[str], message: dict[str, Any]) -> None:
    assert proc.stdin is not None
    proc.stdin.write(json.dumps(message) + "\n")
    proc.stdin.flush()


def _read_until(proc: subprocess.Popen[str], match: Any, timeout: float = 10) -> dict[str, Any]:
    assert proc.stdout is not None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if not line:
            break
        message: dict[str, Any] = json.loads(line)
        if match(message):
            return message
    msg = "expected message never arrived"
    raise AssertionError(msg)


def test_channel_capability_and_push_over_stdio() -> None:
    proc = _spawn()
    try:
        _send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": LATEST_HANDSHAKE_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "claude-code", "version": "test"},
                },
            },
        )
        init = _read_until(proc, lambda m: m.get("id") == 1)
        assert init["result"]["capabilities"]["experimental"]["claude/channel"] == {}
        assert "Instagram DM channel" in init["result"]["instructions"]

        _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        pushed = _read_until(proc, lambda m: m.get("method") == "notifications/claude/channel")
        assert pushed["params"]["content"] == "hey from mqtt"
        assert pushed["params"]["meta"]["chat"] == "ly"
        assert pushed["params"]["meta"]["user"] == "Ly"
        assert pushed["params"]["meta"]["message_id"] == "i1"

        _send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools = _read_until(proc, lambda m: m.get("id") == 2)
        names = {t["name"] for t in tools["result"]["tools"]}
        assert {"subscribe", "unsubscribe", "list_subscriptions", "set_idle"} <= names
    finally:
        proc.kill()
        proc.wait()


def test_channel_capability_and_push_over_modern_protocol() -> None:
    """The middleware also works on a 2026-07-28 connection: server/discover, no handshake."""
    proc = _spawn()
    try:
        _send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "server/discover",
                "params": {"_meta": MODERN_META},
            },
        )
        discover = _read_until(proc, lambda m: m.get("id") == 1)
        assert discover["result"]["capabilities"]["experimental"]["claude/channel"] == {}

        _send(
            proc,
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {"_meta": MODERN_META}},
        )
        pushed = _read_until(proc, lambda m: m.get("method") == "notifications/claude/channel")
        assert pushed["params"]["content"] == "hey from mqtt"
        assert pushed["params"]["meta"]["chat"] == "ly"
        assert pushed["params"]["meta"]["message_id"] == "i1"
    finally:
        proc.kill()
        proc.wait()


def test_claude_code_falls_back_to_the_handshake_and_gets_pushes() -> None:
    """Replay Claude Code's exchange with a channel it delivers (the WhatsApp one).

    It probes with an enveloped server/discover, gets METHOD_NOT_FOUND, then
    initializes at a handshake version on the same connection. At 2026-07-28 it
    drops channel pushes ("no unsolicited notification path").
    """
    proc = _spawn("--claude-code")
    try:
        _send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "server/discover",
                "params": {"_meta": MODERN_META},
            },
        )
        probe = _read_until(proc, lambda m: m.get("id") == 1)
        assert probe["error"]["code"] == -32601

        _send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "initialize",
                "params": {
                    "protocolVersion": LATEST_HANDSHAKE_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "claude-code", "version": "test"},
                },
            },
        )
        init = _read_until(proc, lambda m: m.get("id") == 2)
        assert init["result"]["protocolVersion"] == LATEST_HANDSHAKE_VERSION
        assert init["result"]["capabilities"]["experimental"]["claude/channel"] == {}

        _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        pushed = _read_until(proc, lambda m: m.get("method") == "notifications/claude/channel")
        assert pushed["params"]["content"] == "hey from mqtt"
        assert pushed["params"]["meta"]["chat"] == "ly"
    finally:
        proc.kill()
        proc.wait()
