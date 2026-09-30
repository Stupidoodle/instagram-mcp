"""Log lines as JSON, one object per line, and the same records over OTLP.

Every line has ``time`` (local, with offset, ms), ``level`` (debug info warn error),
``msg`` (a constant sentence, no content), ``service``, ``trace_id`` and ``span_id``
while a span is active, then the flat fields passed as ``extra`` (``message_id``,
``kind``, ``bytes``, ``duration_ms`` ...). An exception adds its class as
``error_type`` and its frames as ``stack``, never its message: messages of HTTP and
Instagram errors carry URLs with thread ids and response bodies.
"""

from __future__ import annotations

import json
import logging
import time
import traceback
from datetime import datetime
from typing import TYPE_CHECKING, Any, TextIO

from opentelemetry import context, trace
from opentelemetry._logs import SeverityNumber

if TYPE_CHECKING:
    from opentelemetry._logs import LoggerProvider

PACKAGE = "instagram_mcp"

_STANDARD = frozenset(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {
    "message",
    "asctime",
    "taskName",
}
_LEVELS = {
    logging.DEBUG: ("debug", SeverityNumber.DEBUG),
    logging.INFO: ("info", SeverityNumber.INFO),
    logging.WARNING: ("warn", SeverityNumber.WARN),
    logging.ERROR: ("error", SeverityNumber.ERROR),
    logging.CRITICAL: ("error", SeverityNumber.FATAL),
}


def _level(levelno: int) -> tuple[str, SeverityNumber]:
    for threshold in (logging.CRITICAL, logging.ERROR, logging.WARNING, logging.INFO):
        if levelno >= threshold:
            return _LEVELS[threshold]
    return _LEVELS[logging.DEBUG]


def fields(record: logging.LogRecord) -> dict[str, Any]:
    """The record's ``extra`` fields, plus an exception's class and frames (not its message)."""
    out = {k: v for k, v in vars(record).items() if k not in _STANDARD and not k.startswith("_")}
    if record.exc_info and record.exc_info[0] is not None:
        exc_type, _, tb = record.exc_info
        out.setdefault("error_type", exc_type.__qualname__)
        if tb is not None:
            out["stack"] = "".join(traceback.format_tb(tb))
    return out


class JsonFormatter(logging.Formatter):
    """One JSON object per record, with the active trace and span id."""

    def __init__(self, service: str) -> None:
        super().__init__()
        self._service = service

    def format(self, record: logging.LogRecord) -> str:
        """Render ``record`` as one JSON line."""
        moment = datetime.fromtimestamp(record.created).astimezone()
        line: dict[str, Any] = {
            "time": moment.isoformat(timespec="milliseconds"),
            "level": _level(record.levelno)[0],
            "msg": record.getMessage(),
            "service": self._service,
        }
        span = trace.get_current_span().get_span_context()
        if span.is_valid:
            line["trace_id"] = trace.format_trace_id(span.trace_id)
            line["span_id"] = trace.format_span_id(span.span_id)
        line["logger"] = record.name
        line |= fields(record)
        return json.dumps(line, default=str, ensure_ascii=False)


class OtlpHandler(logging.Handler):
    """Sends records over OTLP with the active trace context and the JSON line's fields."""

    def __init__(self, provider: LoggerProvider) -> None:
        super().__init__()
        self._provider = provider

    def emit(self, record: logging.LogRecord) -> None:
        """Hand ``record`` to the OpenTelemetry logger of the same name."""
        if record.name.startswith("opentelemetry"):
            return  # the exporter's own complaints stay on stderr, never loop back
        try:
            text, severity = _level(record.levelno)
            attributes = {k: _attribute(v) for k, v in fields(record).items()}
            self._provider.get_logger(record.name).emit(
                timestamp=int(record.created * 1e9),
                observed_timestamp=time.time_ns(),
                context=context.get_current(),
                severity_number=severity,
                severity_text=text.upper(),
                body=record.getMessage(),
                attributes=attributes,
            )
        except Exception:
            self.handleError(record)


class _AtMostWarn(logging.Filter):
    """The OpenTelemetry SDK's own errors (a dead collector) are logged at WARN at most."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name.startswith("opentelemetry") and record.levelno > logging.WARNING:
            record.levelno, record.levelname = logging.WARNING, "WARNING"
        return True


def _attribute(value: Any) -> str | bool | int | float:
    return value if isinstance(value, str | bool | int | float) else str(value)


def setup(
    level: str,
    *,
    service: str,
    stream: TextIO,
    provider: LoggerProvider | None = None,
) -> logging.Logger:
    """Send every log record through one JSON handler (and OTLP, given a provider).

    This package logs at ``level``; every other library at WARNING, since instagrapi
    logs its request URLs, with thread ids and usernames, at INFO. Calling it again
    replaces the handlers it installed before.

    Args:
        level: ``LOG_LEVEL`` for this package's loggers.
        service: The ``service`` field of every line.
        stream: Where the lines go (stderr for an MCP stdio server).
        provider: An OpenTelemetry logger provider to also send records to.

    Returns:
        The package logger.
    """
    root = logging.getLogger()
    for handler in [h for h in root.handlers if isinstance(h, _Owned)]:
        root.removeHandler(handler)
    lines = _OwnedStream(stream)
    lines.setFormatter(JsonFormatter(service))
    lines.addFilter(_AtMostWarn())
    root.addHandler(lines)
    if provider is not None:
        otlp = _OwnedOtlp(provider)
        otlp.addFilter(_AtMostWarn())
        root.addHandler(otlp)
    root.setLevel(logging.WARNING)
    package = logging.getLogger(PACKAGE)
    package.setLevel(getattr(logging, level))
    return package


class _Owned:
    """Marks the handlers ``setup`` installed, so a second call replaces them."""


class _OwnedStream(_Owned, logging.StreamHandler[TextIO]):
    pass


class _OwnedOtlp(_Owned, OtlpHandler):
    pass
