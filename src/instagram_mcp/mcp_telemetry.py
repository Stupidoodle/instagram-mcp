"""Tool-call telemetry for the thin client, on top of the MCP SDK's own spans.

mcp 2.2 already wraps every inbound message in a SERVER span (``tools/call reply``
with ``mcp.method.name``, ``gen_ai.tool.name`` and ``gen_ai.operation.name``) through
its OpenTelemetryMiddleware, first in the chain, so no second span is made here. This
middleware runs inside that span: it adds ``dm.persona``, marks a tool that answered
``success: false`` as a tool error, and records ``mcp.server.operation.duration``.
Tool arguments and results are never recorded.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from mcp.shared.exceptions import MCPError
from opentelemetry import trace
from opentelemetry.trace import StatusCode

from instagram_mcp import instruments

if TYPE_CHECKING:
    from mcp.server.context import (
        CallNext,
        HandlerResult,
        ServerMiddleware,
        ServerRequestContext,
    )


def operation_middleware(persona: str) -> ServerMiddleware[Any]:
    """Server middleware timing every request of the persona's MCP session."""

    async def record(ctx: ServerRequestContext[Any, Any], call_next: CallNext) -> HandlerResult:
        span = trace.get_current_span()  # the SDK's span for this message
        span.set_attribute("dm.persona", persona)
        attributes = {"mcp.method.name": ctx.method, "persona": persona}
        tool = (ctx.params or {}).get("name") if ctx.method == "tools/call" else None
        if isinstance(tool, str):
            attributes["gen_ai.tool.name"] = tool
        start = time.perf_counter()
        try:
            result = await call_next(ctx)
        except MCPError as exc:
            attributes["error.type"] = str(exc.error.code)
            raise
        except Exception as exc:
            attributes["error.type"] = type(exc).__qualname__
            raise
        else:
            if ctx.method == "tools/call" and _failed(result):
                attributes["error.type"] = "tool_error"
                span.set_attribute("error.type", "tool_error")
                span.set_status(StatusCode.ERROR)
            return result
        finally:
            instruments.operation_duration.record(time.perf_counter() - start, attributes)

    return record


def _failed(result: HandlerResult) -> bool:
    """Whether a tool call failed: an error result, or the tool's own ``success: false``."""
    if isinstance(result, dict):
        error, structured = result.get("isError"), result.get("structuredContent")
    else:
        error = getattr(result, "is_error", None)
        structured = getattr(result, "structured_content", None)
    return error is True or (isinstance(structured, dict) and structured.get("success") is False)
