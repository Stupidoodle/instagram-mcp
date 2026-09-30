"""Tool calls: the SDK's tools/call span gets the persona, and every request is timed."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.client import Client
from mcp.server.mcpserver import MCPServer
from mcp.shared.exceptions import MCPError
from opentelemetry.trace import SpanKind, StatusCode

from instagram_mcp.instruments import MCP_BUCKETS
from instagram_mcp.mcp_telemetry import _failed, operation_middleware

if TYPE_CHECKING:
    from tests.support.telemetry import Telemetry

DURATION = "mcp.server.operation.duration"
PRIVATE = "meet me at nine"


def _server() -> MCPServer:
    mcp = MCPServer("t", middleware=[operation_middleware("mini")])

    @mcp.tool()
    def reply(text: str) -> dict[str, Any]:
        """Reply."""
        return {"success": True, "echo": len(text)}

    @mcp.tool()
    def refused(text: str) -> dict[str, Any]:
        """Refuse."""
        return {"success": False, "error": f"refused: {text}"}

    @mcp.tool()
    def crash(text: str) -> str:
        """Crash."""
        raise RuntimeError(text)

    return mcp


def _count(telemetry: Telemetry, tool: str, **labels: str) -> float:
    return telemetry.total(
        DURATION,
        persona="mini",
        **{"mcp.method.name": "tools/call", "gen_ai.tool.name": tool},
        **labels,
    )


async def test_one_sdk_span_per_tool_call_with_the_persona(telemetry: Telemetry) -> None:
    before = _count(telemetry, "reply")
    async with Client(_server()) as client:
        await client.call_tool("reply", {"text": PRIVATE})
    (span,) = telemetry.named("tools/call reply")
    assert span.kind is SpanKind.SERVER
    attributes = dict(span.attributes or {})
    assert attributes["dm.persona"] == "mini"
    assert attributes["gen_ai.tool.name"] == "reply"
    assert attributes["gen_ai.operation.name"] == "execute_tool"
    assert "error.type" not in attributes
    assert PRIVATE not in str(attributes)
    assert _count(telemetry, "reply") == before + 1
    metric = telemetry.metric(DURATION)
    assert metric is not None and metric.unit == "s"
    assert tuple(metric.data.data_points[0].explicit_bounds) == MCP_BUCKETS  # type: ignore[union-attr]
    other = [p for p in telemetry.points(DURATION) if p["mcp.method.name"] != "tools/call"]
    assert other  # the client's discovery and list requests are timed too
    assert all("gen_ai.tool.name" not in p for p in other)


@pytest.mark.parametrize("tool", ["refused", "crash"])
async def test_a_failed_tool_is_a_tool_error(telemetry: Telemetry, tool: str) -> None:
    before = _count(telemetry, tool, **{"error.type": "tool_error"})
    async with Client(_server()) as client:
        await client.call_tool(tool, {"text": PRIVATE})
    (span,) = telemetry.named(f"tools/call {tool}")
    assert span.status.status_code is StatusCode.ERROR
    assert dict(span.attributes or {})["error.type"] == "tool_error"
    assert _count(telemetry, tool, **{"error.type": "tool_error"}) == before + 1
    assert PRIVATE not in str([dict(s.attributes or {}) for s in telemetry.spans()])


async def test_protocol_errors_and_crashes_are_typed(telemetry: Telemetry) -> None:
    record = operation_middleware("mini")
    ctx = MagicMock(method="tools/call", params={"name": "reply"})
    with pytest.raises(MCPError):
        await record(ctx, AsyncMock(side_effect=MCPError(-32602, "bad")))
    with pytest.raises(KeyError):
        await record(MagicMock(method="ping", params=None), AsyncMock(side_effect=KeyError))
    assert _count(telemetry, "reply", **{"error.type": "-32602"}) >= 1
    ping = {"mcp.method.name": "ping", "error.type": "KeyError"}
    assert telemetry.total(DURATION, persona="mini", **ping) >= 1


def test_failed_results() -> None:
    assert _failed({"isError": True}) is True
    assert _failed({"structuredContent": {"success": False}}) is True
    assert _failed({"structuredContent": {"success": True}}) is False
    assert _failed(MagicMock(is_error=False, structured_content=None)) is False
    assert _failed(None) is False
