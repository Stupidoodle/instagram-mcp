"""JSON log lines and their OTLP twin: fields, trace ids, exceptions without messages."""

from __future__ import annotations

import io
import json
import logging
import re
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from opentelemetry import _logs, trace

from instagram_mcp import logs
from instagram_mcp.config import setup_logging
from instagram_mcp.telemetry import Providers

if TYPE_CHECKING:
    from tests.support.telemetry import Telemetry

PRIVATE_TEXT = "thread 340282366841700000000000000000000000001 said hi"


pytestmark = pytest.mark.usefixtures("restore_logging")


def _setup(level: str = "INFO", **kwargs: Any) -> io.StringIO:
    stream = io.StringIO()
    logs.setup(level, service="instagram-bridge", stream=stream, **kwargs)
    return stream


def _lines(stream: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


class TestJsonLines:
    def test_fields(self) -> None:
        stream = _setup()
        logging.getLogger("instagram_mcp.bridge").info(
            "Voice note transcribed", extra={"message_id": "i1", "duration_ms": 12}
        )
        (line,) = _lines(stream)
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}[+-]\d\d:\d\d", line["time"])
        assert line["level"] == "info"
        assert line["msg"] == "Voice note transcribed"
        assert line["service"] == "instagram-bridge"
        assert line["logger"] == "instagram_mcp.bridge"
        assert (line["message_id"], line["duration_ms"]) == ("i1", 12)
        assert "trace_id" not in line

    def test_levels(self) -> None:
        stream = _setup("DEBUG")
        log = logging.getLogger("instagram_mcp.x")
        for level in (logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR, 50):
            log.log(level, "m")
        assert [x["level"] for x in _lines(stream)] == ["debug", "info", "warn", "error", "error"]

    def test_trace_and_span_ids_inside_a_span(self, telemetry: Telemetry) -> None:
        stream = _setup()
        with trace.get_tracer("t").start_as_current_span("s") as span:
            logging.getLogger("instagram_mcp.x").warning("m")
        (line,) = _lines(stream)
        assert line["trace_id"] == trace.format_trace_id(span.get_span_context().trace_id)
        assert line["span_id"] == trace.format_span_id(span.get_span_context().span_id)
        assert len(line["trace_id"]) == 32 and len(line["span_id"]) == 16

    def test_an_exception_keeps_its_class_and_frames_not_its_message(self) -> None:
        stream = _setup()
        try:
            raise RuntimeError(PRIVATE_TEXT)
        except RuntimeError:
            logging.getLogger("instagram_mcp.x").exception("Push failed")
        (line,) = _lines(stream)
        assert line["error_type"] == "RuntimeError"
        assert "test_logs.py" in line["stack"]
        assert "340282366841700000000000000000000000001" not in json.dumps(line)

    def test_other_libraries_log_warnings_only(self) -> None:
        stream = _setup("DEBUG")
        logging.getLogger("private_request").info("GET /direct_v2/threads/%s/", PRIVATE_TEXT)
        logging.getLogger("instagrapi").warning("rate limited")
        assert [x["msg"] for x in _lines(stream)] == ["rate limited"]

    def test_the_sdks_own_errors_are_warnings(self) -> None:
        stream = _setup()
        logging.getLogger("opentelemetry.exporter.otlp").error("Failed to export")
        assert _lines(stream)[0]["level"] == "warn"

    def test_a_second_setup_replaces_the_first(self) -> None:
        first = _setup()
        second = _setup()
        logging.getLogger("instagram_mcp.x").warning("once")
        assert first.getvalue() == ""
        assert len(_lines(second)) == 1


class TestOtlp:
    def test_records_go_out_with_the_trace_and_the_fields(self, telemetry: Telemetry) -> None:
        _setup(provider=_logs.get_logger_provider())
        with trace.get_tracer("t").start_as_current_span("s") as span:
            logging.getLogger("instagram_mcp.x").warning("Push failed", extra={"kind": "text"})
        logging.getLogger("opentelemetry.exporter").warning("export failed")
        (record,) = [r.log_record for r in telemetry.logs.get_finished_logs()]
        assert record.body == "Push failed"
        assert record.severity_text == "WARN"
        assert record.attributes is not None and record.attributes["kind"] == "text"
        assert record.trace_id == span.get_span_context().trace_id

    def test_a_failing_provider_does_not_raise(self) -> None:
        class Broken:
            def get_logger(self, _name: str) -> None:
                raise RuntimeError

        handler = logs.OtlpHandler(Broken())  # type: ignore[arg-type]
        record = logging.LogRecord("instagram_mcp", logging.INFO, "", 0, "m", None, None)
        with patch.object(handler, "handleError") as handled:
            handler.emit(record)
        handled.assert_called_once_with(record)

    def test_non_primitive_fields_become_text(self) -> None:
        assert logs._attribute(3) == 3
        assert logs._attribute(["a"]) == "['a']"


class TestSetupLogging:
    def test_writes_to_stderr_by_default(self, capsys: pytest.CaptureFixture[str]) -> None:
        logger = setup_logging("INFO")
        logger.warning("to stderr")
        captured = capsys.readouterr()
        assert json.loads(captured.err.splitlines()[-1])["service"] == "instagram-mcp"
        assert captured.out == ""

    def test_the_bridge_writes_to_stdout(self) -> None:
        stream = io.StringIO()
        setup_logging("INFO", service="instagram-bridge", stream=stream)
        logging.getLogger("instagram_mcp.bridge").info("up")
        assert _lines(stream)[0]["service"] == "instagram-bridge"

    def test_ships_logs_when_telemetry_installed_a_logger(self) -> None:
        providers = Providers(None, None, _logs.get_logger_provider())  # type: ignore[arg-type]
        with patch("instagram_mcp.config.telemetry.installed", return_value=providers):
            setup_logging("INFO", stream=io.StringIO())
        assert any(isinstance(h, logs.OtlpHandler) for h in logging.getLogger().handlers)
