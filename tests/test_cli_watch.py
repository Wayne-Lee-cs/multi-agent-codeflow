"""Tests for cagent/cli/watch.py — dashboard display, status, and watch commands."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cagent.cli.watch import (
    _load_budget,
    _print_dashboard_table,
)


class TestLoadBudget:
    """Tests for _load_budget helper."""

    def test_loads_budget_from_file(self, tmp_path: Path) -> None:
        """Returns max_tokens when budget.json exists and is valid."""
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "budget.json").write_text(
            json.dumps({"max_tokens": 50000}), encoding="utf-8"
        )
        assert _load_budget(run_dir) == 50000

    def test_no_budget_file(self, tmp_path: Path) -> None:
        """Returns None when budget.json doesn't exist."""
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        assert _load_budget(run_dir) is None

    def test_empty_max_tokens(self, tmp_path: Path) -> None:
        """Returns None when max_tokens key is missing."""
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "budget.json").write_text(
            json.dumps({"other": "data"}), encoding="utf-8"
        )
        assert _load_budget(run_dir) is None

    def test_null_max_tokens(self, tmp_path: Path) -> None:
        """Returns None when max_tokens is null."""
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "budget.json").write_text(
            json.dumps({"max_tokens": None}), encoding="utf-8"
        )
        assert _load_budget(run_dir) is None

    def test_corrupt_json(self, tmp_path: Path) -> None:
        """Returns None when budget.json is corrupt."""
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "budget.json").write_text("not json {{{", encoding="utf-8")
        assert _load_budget(run_dir) is None

    def test_string_max_tokens_converted(self, tmp_path: Path) -> None:
        """Converts string max_tokens to int."""
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "budget.json").write_text(
            json.dumps({"max_tokens": "75000"}), encoding="utf-8"
        )
        assert _load_budget(run_dir) == 75000


class TestPrintDashboardTableExtra:
    """Additional tests for _print_dashboard_table covering uncovered paths."""

    def test_commit_sha_in_activity(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Activity column shows commit sha when available."""
        data = {
            "001": {
                "status": "done",
                "started_at": 1000.0,
                "ended_at": 1060.0,
                "tool_count": 3,
                "commit_sha": "abc1234567890",
            },
        }
        _print_dashboard_table("run-1", data)
        out = capsys.readouterr().out
        assert "commit abc1234" in out

    def test_fail_reason_in_activity(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Activity column shows fail_reason when commit_sha absent."""
        data = {
            "001": {
                "status": "failed",
                "started_at": 1000.0,
                "ended_at": 1030.0,
                "tool_count": 1,
                "fail_reason": "timeout exceeded after 30m",
            },
        }
        _print_dashboard_table("run-1", data)
        out = capsys.readouterr().out
        assert "timeout exceeded" in out

    def test_no_tokens_table_layout(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Table without tokens uses the narrow layout (no tokens column)."""
        data = {
            "001": {
                "status": "done",
                "started_at": 1000.0,
                "ended_at": 1060.0,
                "tool_count": 2,
                "last_activity": "finished",
            },
        }
        _print_dashboard_table("run-1", data)
        out = capsys.readouterr().out
        # Narrow table has activity column header
        assert "activity" in out.lower()
        # Should not have tokens column header
        assert "tokens" not in out.lower().split("activity")[0]

    def test_elapsed_time_minutes(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Elapsed >= 60s shows XmYs format."""
        now = time.time()
        data = {
            "001": {
                "status": "running",
                "started_at": now - 150.0,  # 2m30s ago
                "tool_count": 1,
                "last_activity": "working",
            },
        }
        _print_dashboard_table("run-1", data)
        out = capsys.readouterr().out
        assert "2m" in out

    def test_elapsed_time_seconds(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Elapsed < 60s shows Ns format."""
        now = time.time()
        data = {
            "001": {
                "status": "running",
                "started_at": now - 15.0,
                "tool_count": 1,
                "last_activity": "working",
            },
        }
        _print_dashboard_table("run-1", data)
        out = capsys.readouterr().out
        # Should show Ns (e.g., "14s" or "15s")
        assert "s" in out

    def test_empty_activity(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Task with no activity fields shows empty activity column."""
        data = {
            "001": {
                "status": "pending",
                "tool_count": 0,
            },
        }
        _print_dashboard_table("run-1", data)
        out = capsys.readouterr().out
        assert "pending" in out

    def test_only_running_no_done(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Status bar shows running count when nothing is done."""
        data = {
            "001": {"status": "running", "started_at": time.time(), "tool_count": 1},
            "002": {"status": "running", "started_at": time.time(), "tool_count": 0},
        }
        _print_dashboard_table("run-1", data)
        out = capsys.readouterr().out
        assert "0/2 done" in out
        assert "2 running" in out

    def test_all_done(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Status bar shows full count when all done."""
        data = {
            "001": {"status": "done", "started_at": 1000, "ended_at": 1010, "tool_count": 2},
            "002": {"status": "done", "started_at": 1000, "ended_at": 1020, "tool_count": 3},
        }
        _print_dashboard_table("run-1", data)
        out = capsys.readouterr().out
        assert "2/2 done" in out

    def test_budget_high_usage_no_color(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Budget >= 80% without TTY has no color."""
        data = {
            "001": {
                "status": "done",
                "started_at": 1000,
                "ended_at": 1010,
                "tool_count": 5,
                "tokens_in": 45000,
                "tokens_out": 10000,
                "last_activity": "done",
            },
        }
        # sys.stdout.isatty() returns False in test, so no color
        _print_dashboard_table("run-1", data, max_tokens=50000)
        out = capsys.readouterr().out
        assert "110%" in out  # 55000/50000 = 110%

    def test_budget_with_color(self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        """Budget >= 80% with TTY shows yellow warning color."""
        data = {
            "001": {
                "status": "done",
                "started_at": 1000,
                "ended_at": 1010,
                "tool_count": 5,
                "tokens_in": 45000,
                "tokens_out": 10000,
                "last_activity": "done",
            },
        }
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        _print_dashboard_table("run-1", data, max_tokens=50000)
        out = capsys.readouterr().out
        assert "\033[33m" in out  # yellow for >= 80%

    def test_budget_low_usage_with_max_tokens(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Budget < 80% shows budget info without warning color."""
        data = {
            "001": {
                "status": "done",
                "started_at": 1000,
                "ended_at": 1010,
                "tool_count": 2,
                "tokens_in": 5000,
                "tokens_out": 2000,
                "last_activity": "done",
            },
        }
        _print_dashboard_table("run-1", data, max_tokens=100000)
        out = capsys.readouterr().out
        assert "100,000 budget" in out
        assert "7%" in out

    def test_no_tokens_no_total_line(self, capsys: pytest.CaptureFixture[str]) -> None:
        """When no tokens are present, no total tokens line is printed."""
        data = {
            "001": {
                "status": "done",
                "started_at": 1000,
                "ended_at": 1010,
                "tool_count": 2,
                "last_activity": "done",
            },
        }
        _print_dashboard_table("run-1", data)
        out = capsys.readouterr().out
        assert "Total tokens" not in out

    def test_sorted_task_ids(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Tasks are displayed in sorted order by ID."""
        data = {
            "003": {"status": "done", "started_at": 1000, "ended_at": 1010, "tool_count": 1},
            "001": {"status": "done", "started_at": 1000, "ended_at": 1010, "tool_count": 1},
            "002": {"status": "done", "started_at": 1000, "ended_at": 1010, "tool_count": 1},
        }
        _print_dashboard_table("run-1", data)
        out = capsys.readouterr().out
        pos1 = out.index("001")
        pos2 = out.index("002")
        pos3 = out.index("003")
        assert pos1 < pos2 < pos3


class TestCmdStatus:
    """Tests for _cmd_status."""

    def test_status_no_dashboard_file(self, tmp_path: Path) -> None:
        """Exits with error when dashboard.json doesn't exist."""
        from cagent.cli.watch import _cmd_status

        run_dir = tmp_path / "run"
        run_dir.mkdir()

        args = MagicMock()
        args.run_id = "test-run"

        with patch("cagent.cli.watch._get_repo_root", return_value=tmp_path), \
             patch("cagent.cli.watch._find_run_dir", return_value=run_dir), \
             pytest.raises(SystemExit, match="1"):
            _cmd_status(args)

    def test_status_with_valid_data(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Prints dashboard table with valid data."""
        from cagent.cli.watch import _cmd_status

        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "dashboard.json").write_text(
            json.dumps({
                "001": {"status": "done", "started_at": 1000, "ended_at": 1010, "tool_count": 2},
            }),
            encoding="utf-8",
        )

        args = MagicMock()
        args.run_id = "test-run"

        with patch("cagent.cli.watch._get_repo_root", return_value=tmp_path), \
             patch("cagent.cli.watch._find_run_dir", return_value=run_dir):
            _cmd_status(args)

        out = capsys.readouterr().out
        assert "run" in out.lower()
        assert "001" in out

    def test_status_corrupt_dashboard(self, tmp_path: Path) -> None:
        """Exits with error when dashboard.json is corrupt."""
        from cagent.cli.watch import _cmd_status

        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "dashboard.json").write_text("not json", encoding="utf-8")

        args = MagicMock()
        args.run_id = "test-run"

        with patch("cagent.cli.watch._get_repo_root", return_value=tmp_path), \
             patch("cagent.cli.watch._find_run_dir", return_value=run_dir), \
             pytest.raises(SystemExit, match="1"):
            _cmd_status(args)


class TestCmdWatchNonTTY:
    """Tests for _cmd_watch non-TTY fallback."""

    def test_non_tty_falls_back_to_status(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """When stdin is not a TTY, watch falls back to status + message."""
        from cagent.cli.watch import _cmd_watch

        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "dashboard.json").write_text(
            json.dumps({
                "001": {"status": "done", "started_at": 1000, "ended_at": 1010, "tool_count": 1},
            }),
            encoding="utf-8",
        )

        args = MagicMock()
        args.run_id = "test-run"
        args.web = None

        with patch("cagent.cli.watch.is_tty", return_value=False), \
             patch("cagent.cli.watch._get_repo_root", return_value=tmp_path), \
             patch("cagent.cli.watch._find_run_dir", return_value=run_dir):
            _cmd_watch(args)

        out = capsys.readouterr().out
        assert "not a terminal" in out


class TestCmdWatchTTY:
    """Tests for _cmd_watch TTY loop paths."""

    def test_watch_exits_on_q_key(self, tmp_path: Path) -> None:
        """Watch loop exits when 'q' key is pressed."""
        from cagent.cli.watch import _cmd_watch

        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "dashboard.json").write_text(
            json.dumps({"001": {"status": "done", "tool_count": 1}}),
            encoding="utf-8",
        )

        args = MagicMock()
        args.run_id = "test-run"
        args.web = None

        call_count = 0

        def mock_stdin_has_key():
            nonlocal call_count
            call_count += 1
            return call_count >= 2  # First call: no key, second call: key available

        with patch("cagent.cli.watch.is_tty", return_value=True), \
             patch("cagent.cli.watch._get_repo_root", return_value=tmp_path), \
             patch("cagent.cli.watch._find_run_dir", return_value=run_dir), \
             patch("cagent.cli.watch.enable_ansi"), \
             patch("cagent.cli.watch.stdin_has_key", side_effect=mock_stdin_has_key), \
             patch("cagent.cli.watch.read_key", return_value="q"), \
             patch("cagent.cli.watch.time.sleep"):
            _cmd_watch(args)

    def test_watch_exits_on_uppercase_q(self, tmp_path: Path) -> None:
        """Watch loop exits when 'Q' key is pressed."""
        from cagent.cli.watch import _cmd_watch

        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "dashboard.json").write_text(
            json.dumps({"001": {"status": "done", "tool_count": 1}}),
            encoding="utf-8",
        )

        args = MagicMock()
        args.run_id = "test-run"
        args.web = None

        with patch("cagent.cli.watch.is_tty", return_value=True), \
             patch("cagent.cli.watch._get_repo_root", return_value=tmp_path), \
             patch("cagent.cli.watch._find_run_dir", return_value=run_dir), \
             patch("cagent.cli.watch.enable_ansi"), \
             patch("cagent.cli.watch.stdin_has_key", return_value=True), \
             patch("cagent.cli.watch.read_key", return_value="Q"), \
             patch("cagent.cli.watch.time.sleep"):
            _cmd_watch(args)

    def test_watch_redraws_on_data_change(self, tmp_path: Path, capsys) -> None:
        """Watch loop redraws when dashboard data changes."""
        from cagent.cli.watch import _cmd_watch

        run_dir = tmp_path / "run"
        run_dir.mkdir()
        dashboard_data = {"001": {"status": "running", "tool_count": 1, "started_at": 1000}}
        (run_dir / "dashboard.json").write_text(
            json.dumps(dashboard_data), encoding="utf-8"
        )

        args = MagicMock()
        args.run_id = "test-run"
        args.web = None

        loop_count = 0

        def mock_sleep(duration):
            nonlocal loop_count
            loop_count += 1
            if loop_count >= 2:
                raise KeyboardInterrupt("stop")

        # Use real filesystem - stat and read will work normally.
        # Only mock stdin/key/sleep to control the loop.
        with patch("cagent.cli.watch.is_tty", return_value=True), \
             patch("cagent.cli.watch._get_repo_root", return_value=tmp_path), \
             patch("cagent.cli.watch._find_run_dir", return_value=run_dir), \
             patch("cagent.cli.watch.enable_ansi"), \
             patch("cagent.cli.watch.stdin_has_key", return_value=False), \
             patch("cagent.cli.watch.time.sleep", side_effect=mock_sleep):
            try:
                _cmd_watch(args)
            except KeyboardInterrupt:
                pass

        out = capsys.readouterr().out
        assert "running" in out

    def test_watch_handles_corrupt_json(self, tmp_path: Path) -> None:
        """Watch loop handles corrupt JSON in dashboard file."""
        from cagent.cli.watch import _cmd_watch

        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "dashboard.json").write_text("not json", encoding="utf-8")

        args = MagicMock()
        args.run_id = "test-run"
        args.web = None

        loop_count = 0

        def mock_sleep(duration):
            nonlocal loop_count
            loop_count += 1
            if loop_count >= 2:
                raise KeyboardInterrupt("stop")

        # Real filesystem - stat works but json.loads fails on corrupt data
        with patch("cagent.cli.watch.is_tty", return_value=True), \
             patch("cagent.cli.watch._get_repo_root", return_value=tmp_path), \
             patch("cagent.cli.watch._find_run_dir", return_value=run_dir), \
             patch("cagent.cli.watch.enable_ansi"), \
             patch("cagent.cli.watch.stdin_has_key", return_value=False), \
             patch("cagent.cli.watch.time.sleep", side_effect=mock_sleep):
            try:
                _cmd_watch(args)
            except KeyboardInterrupt:
                pass

    def test_watch_no_dashboard_file(self, tmp_path: Path) -> None:
        """Watch loop handles missing dashboard file gracefully."""
        from cagent.cli.watch import _cmd_watch

        run_dir = tmp_path / "run"
        run_dir.mkdir()
        # No dashboard.json

        args = MagicMock()
        args.run_id = "test-run"
        args.web = None

        loop_count = 0

        def mock_sleep(duration):
            nonlocal loop_count
            loop_count += 1
            if loop_count >= 2:
                raise KeyboardInterrupt("stop")

        with patch("cagent.cli.watch.is_tty", return_value=True), \
             patch("cagent.cli.watch._get_repo_root", return_value=tmp_path), \
             patch("cagent.cli.watch._find_run_dir", return_value=run_dir), \
             patch("cagent.cli.watch.enable_ansi"), \
             patch("cagent.cli.watch.stdin_has_key", return_value=False), \
             patch("cagent.cli.watch.time.sleep", side_effect=mock_sleep):
            try:
                _cmd_watch(args)
            except KeyboardInterrupt:
                pass


class TestCmdWatchWeb:
    """Tests for _cmd_watch_web."""

    def test_watch_web_starts_server(self, tmp_path: Path) -> None:
        """_cmd_watch_web starts the dashboard server."""
        from cagent.cli.watch import _cmd_watch_web

        run_dir = tmp_path / "run"
        run_dir.mkdir()

        args = MagicMock()
        args.run_id = "test-run"
        args.web = 8080

        # Close the coroutine passed to asyncio.run so it isn't left un-awaited
        # (which would emit "coroutine was never awaited" RuntimeWarning at GC).
        def _consume(coro):
            if hasattr(coro, "close"):
                coro.close()

        with patch("cagent.cli.watch._get_repo_root", return_value=tmp_path), \
             patch("cagent.cli.watch._find_run_dir", return_value=run_dir), \
             patch("asyncio.run", side_effect=_consume) as mock_run:
            _cmd_watch_web(args)

        mock_run.assert_called_once()


class TestPrintDashboardTableAnsiColors:
    """Tests for ANSI color paths in _print_dashboard_table."""

    def test_done_status_green_color(self, capsys, monkeypatch):
        """Done status shows green ANSI color when TTY."""
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        data = {
            "001": {"status": "done", "started_at": 1000, "ended_at": 1010, "tool_count": 1},
        }
        _print_dashboard_table("run-1", data)
        out = capsys.readouterr().out
        assert "\033[32m" in out  # green

    def test_failed_status_red_color(self, capsys, monkeypatch):
        """Failed status shows red ANSI color when TTY."""
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        data = {
            "001": {"status": "failed", "started_at": 1000, "ended_at": 1010, "tool_count": 1, "fail_reason": "timeout"},
        }
        _print_dashboard_table("run-1", data)
        out = capsys.readouterr().out
        assert "\033[31m" in out  # red

    def test_running_status_yellow_color(self, capsys, monkeypatch):
        """Running status shows yellow ANSI color when TTY."""
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        data = {
            "001": {"status": "running", "started_at": 1000, "tool_count": 1},
        }
        _print_dashboard_table("run-1", data)
        out = capsys.readouterr().out
        assert "\033[33m" in out  # yellow

    def test_fail_reason_red_in_activity(self, capsys, monkeypatch):
        """fail_reason without commit_sha shows red in activity column when TTY."""
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        data = {
            "001": {
                "status": "failed",
                "started_at": 1000,
                "ended_at": 1010,
                "tool_count": 1,
                "fail_reason": "crashed badly",
            },
        }
        _print_dashboard_table("run-1", data)
        out = capsys.readouterr().out
        assert "\033[31m" in out
        assert "crashed badly" in out

    def test_unknown_status_no_color(self, capsys, monkeypatch):
        """Unknown status has no ANSI color."""
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        data = {
            "001": {"status": "pending", "tool_count": 0},
        }
        _print_dashboard_table("run-1", data)
        out = capsys.readouterr().out
        assert "\033[32m" not in out
        assert "\033[31m" not in out
        assert "\033[33m" not in out
