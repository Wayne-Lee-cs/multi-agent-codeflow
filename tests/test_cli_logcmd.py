"""Tests for cagent/cli/logcmd.py - log command and event formatting."""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from cagent.cli.logcmd import (
    _print_event_line,
    _print_events_formatted,
    _cmd_log,
    _follow_file,
    _follow_events_formatted,
)


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


class TestPrintEventsFormatted:
    """Tests for _print_events_formatted reading from a file."""

    def test_reads_all_lines(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Should print formatted output for each valid JSON line."""
        events_file = tmp_path / "events.jsonl"
        lines = [
            json.dumps({"kind": "text", "ts": 1700000000.0, "summary": "hello"}),
            json.dumps({"kind": "done", "ts": 1700000001.0, "summary": "finished"}),
        ]
        events_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

        _print_events_formatted(events_file, None)
        out = capsys.readouterr().out
        assert "hello" in out
        assert "finished" in out

    def test_skips_blank_lines(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Blank lines should be silently skipped."""
        events_file = tmp_path / "events.jsonl"
        events_file.write_text(
            '{"kind": "text", "ts": 1, "summary": "ok"}\n\n\n',
            encoding="utf-8",
        )

        _print_events_formatted(events_file, None)
        out = capsys.readouterr().out
        assert "ok" in out

    def test_applies_kind_filter(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Should only show events matching the kind filter."""
        events_file = tmp_path / "events.jsonl"
        lines = [
            json.dumps({"kind": "tool_use", "ts": 1, "summary": "reading"}),
            json.dumps({"kind": "error", "ts": 2, "summary": "failed"}),
        ]
        events_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

        _print_events_formatted(events_file, "error")
        out = capsys.readouterr().out
        assert "failed" in out
        assert "reading" not in out


class TestCmdLog:
    """Tests for _cmd_log command handler."""

    def test_events_file_not_found(self, tmp_path: Path) -> None:
        """Exit with error when events file doesn't exist."""
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "events").mkdir()

        args = MagicMock()
        args.task_id = "task-999"
        args.run = "my-run"
        args.raw = False
        args.follow = False
        args.kind = None

        with patch("cagent.cli.logcmd._get_repo_root", return_value=tmp_path), \
             patch("cagent.cli.logcmd._find_run_dir", return_value=run_dir), \
             pytest.raises(SystemExit, match="1"):
            _cmd_log(args)

    def test_raw_mode_prints_file_content(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Raw mode prints the file as-is."""
        run_dir = tmp_path / "run"
        events_dir = run_dir / "events"
        events_dir.mkdir(parents=True)
        events_file = events_dir / "task-001.jsonl"
        raw_content = '{"raw": true}\n{"raw": true}\n'
        events_file.write_text(raw_content, encoding="utf-8")

        args = MagicMock()
        args.task_id = "001"
        args.run = "my-run"
        args.raw = True
        args.follow = False
        args.kind = None

        with patch("cagent.cli.logcmd._get_repo_root", return_value=tmp_path), \
             patch("cagent.cli.logcmd._find_run_dir", return_value=run_dir):
            _cmd_log(args)

        out = capsys.readouterr().out
        assert '{"raw": true}' in out

    def test_formatted_mode(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Non-raw mode formats events as human-readable."""
        run_dir = tmp_path / "run"
        events_dir = run_dir / "events"
        events_dir.mkdir(parents=True)
        events_file = events_dir / "task-001.jsonl"
        events_file.write_text(
            json.dumps({"kind": "done", "ts": 1700000000.0, "summary": "completed"}) + "\n",
            encoding="utf-8",
        )

        args = MagicMock()
        args.task_id = "001"
        args.run = "my-run"
        args.raw = False
        args.follow = False
        args.kind = None

        with patch("cagent.cli.logcmd._get_repo_root", return_value=tmp_path), \
             patch("cagent.cli.logcmd._find_run_dir", return_value=run_dir):
            _cmd_log(args)

        out = capsys.readouterr().out
        assert "completed" in out

    def test_task_id_prefix_stripped(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """task- prefix should be stripped from task_id before lookup."""
        run_dir = tmp_path / "run"
        events_dir = run_dir / "events"
        events_dir.mkdir(parents=True)
        events_file = events_dir / "task-005.jsonl"
        events_file.write_text(
            json.dumps({"kind": "text", "ts": 1, "summary": "ok"}) + "\n",
            encoding="utf-8",
        )

        args = MagicMock()
        args.task_id = "task-005"
        args.run = None
        args.raw = False
        args.follow = False
        args.kind = None

        with patch("cagent.cli.logcmd._get_repo_root", return_value=tmp_path), \
             patch("cagent.cli.logcmd._find_run_dir", return_value=run_dir):
            _cmd_log(args)

        out = capsys.readouterr().out
        assert "ok" in out

    def test_follow_raw_mode_keyboard_interrupt(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Follow raw mode handles KeyboardInterrupt gracefully."""
        run_dir = tmp_path / "run"
        events_dir = run_dir / "events"
        events_dir.mkdir(parents=True)
        events_file = events_dir / "task-001.jsonl"
        events_file.write_text("", encoding="utf-8")

        args = MagicMock()
        args.task_id = "001"
        args.run = None
        args.raw = True
        args.follow = True
        args.kind = None

        with patch("cagent.cli.logcmd._get_repo_root", return_value=tmp_path), \
             patch("cagent.cli.logcmd._find_run_dir", return_value=run_dir), \
             patch("cagent.cli.logcmd.time.sleep", side_effect=KeyboardInterrupt):
            _cmd_log(args)

        out = capsys.readouterr().out
        assert "Ctrl+C" in out

    def test_follow_formatted_keyboard_interrupt(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Follow formatted mode handles KeyboardInterrupt gracefully."""
        run_dir = tmp_path / "run"
        events_dir = run_dir / "events"
        events_dir.mkdir(parents=True)
        events_file = events_dir / "task-001.jsonl"
        events_file.write_text("", encoding="utf-8")

        args = MagicMock()
        args.task_id = "001"
        args.run = None
        args.raw = False
        args.follow = True
        args.kind = None

        with patch("cagent.cli.logcmd._get_repo_root", return_value=tmp_path), \
             patch("cagent.cli.logcmd._find_run_dir", return_value=run_dir), \
             patch("cagent.cli.logcmd.time.sleep", side_effect=KeyboardInterrupt):
            _cmd_log(args)

        out = capsys.readouterr().out
        assert "Ctrl+C" in out


class TestFollowFile:
    """Tests for _follow_file (raw tail -f)."""

    def test_follow_file_keyboard_interrupt(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """_follow_file handles KeyboardInterrupt gracefully."""
        f = tmp_path / "test.log"
        f.write_text("line1\n", encoding="utf-8")

        with patch("cagent.cli.logcmd.time.sleep", side_effect=KeyboardInterrupt):
            _follow_file(f)

        out = capsys.readouterr().out
        assert "Ctrl+C" in out

    def test_follow_file_reads_content(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """_follow_file reads and prints available content."""
        f = tmp_path / "test.log"
        f.write_text("hello world\n", encoding="utf-8")

        call_count = 0

        def mock_sleep(duration):
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                raise KeyboardInterrupt

        with patch("cagent.cli.logcmd.time.sleep", side_effect=mock_sleep):
            _follow_file(f)

        out = capsys.readouterr().out
        assert "hello world" in out


class TestFollowEventsFormatted:
    """Tests for _follow_events_formatted."""

    def test_follow_reads_initial_content(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Reads existing content and prints formatted events."""
        f = tmp_path / "events.jsonl"
        f.write_text(
            json.dumps({"kind": "done", "ts": 1, "summary": "finished"}) + "\n",
            encoding="utf-8",
        )

        call_count = 0

        def mock_sleep(duration):
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                raise KeyboardInterrupt

        with patch("cagent.cli.logcmd.time.sleep", side_effect=mock_sleep):
            _follow_events_formatted(f, None)

        out = capsys.readouterr().out
        assert "Ctrl+C" in out
        assert "finished" in out
