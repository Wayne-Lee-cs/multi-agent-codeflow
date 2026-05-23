"""Tests for cagent/cli/logcmd.py - log command and event formatting."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from cagent.cli.logcmd import _print_event_line


class TestPrintEventLine:
    """Tests for _print_event_line function."""

    def test_tool_use_event(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Tool use event should be formatted with cyan color."""
        line = json.dumps({"kind": "tool_use", "ts": 1700000000.0, "summary": "Read file.py"})
        _print_event_line(line, None)
        captured = capsys.readouterr()
        assert "tool_use" in captured.out
        assert "Read file.py" in captured.out
        assert "\033[36m" in captured.out  # cyan

    def test_error_event(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Error event should be formatted with red color."""
        line = json.dumps({"kind": "error", "ts": 1700000000.0, "summary": "something failed"})
        _print_event_line(line, None)
        captured = capsys.readouterr()
        assert "error" in captured.out
        assert "something failed" in captured.out
        assert "\033[31m" in captured.out  # red

    def test_denied_event(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Denied event should be formatted with yellow color."""
        line = json.dumps({"kind": "denied", "ts": 1700000000.0, "summary": "access denied"})
        _print_event_line(line, None)
        captured = capsys.readouterr()
        assert "denied" in captured.out
        assert "\033[33m" in captured.out  # yellow

    def test_done_event(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Done event should be formatted with green color."""
        line = json.dumps({"kind": "done", "ts": 1700000000.0, "summary": "commit abc1234"})
        _print_event_line(line, None)
        captured = capsys.readouterr()
        assert "done" in captured.out
        assert "commit abc1234" in captured.out
        assert "\033[32m" in captured.out  # green

    def test_text_event(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Text event should be formatted with white color."""
        line = json.dumps({"kind": "text", "ts": 1700000000.0, "summary": "thinking"})
        _print_event_line(line, None)
        captured = capsys.readouterr()
        assert "text" in captured.out
        assert "thinking" in captured.out
        assert "\033[37m" in captured.out  # white

    def test_kind_filter_matches(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Should only show events matching kind_filter."""
        line = json.dumps({"kind": "tool_use", "ts": 1700000000.0, "summary": "Read"})
        _print_event_line(line, "tool_use")
        captured = capsys.readouterr()
        assert "Read" in captured.out

    def test_kind_filter_no_match(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Should not show events not matching kind_filter."""
        line = json.dumps({"kind": "tool_use", "ts": 1700000000.0, "summary": "Read"})
        _print_event_line(line, "error")
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_invalid_json_skipped(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Invalid JSON lines should be silently skipped."""
        _print_event_line("not valid json {{{", None)
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_empty_line_skipped(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Empty lines should be silently skipped."""
        _print_event_line("", None)
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_missing_fields_handled(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Events with missing fields should still be formatted."""
        line = json.dumps({"kind": "text"})
        _print_event_line(line, None)
        captured = capsys.readouterr()
        assert "text" in captured.out
        assert "??:??:??" in captured.out  # missing timestamp

    def test_timestamp_formatted(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Timestamp should be formatted as HH:MM:SS."""
        ts = datetime(2024, 1, 1, 14, 30, 45).timestamp()
        line = json.dumps({"kind": "text", "ts": ts, "summary": "test"})
        _print_event_line(line, None)
        captured = capsys.readouterr()
        assert "14:30:45" in captured.out

    def test_unknown_kind_no_color(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Unknown kind should have no color."""
        line = json.dumps({"kind": "unknown", "ts": 1700000000.0, "summary": "test"})
        _print_event_line(line, None)
        captured = capsys.readouterr()
        assert "unknown" in captured.out
        # No ANSI color codes for unknown kind
        assert "\033[3" not in captured.out
