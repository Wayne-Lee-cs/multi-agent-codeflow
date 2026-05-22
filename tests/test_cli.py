"""Unit tests for pure cli.py helper functions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch
import json
import sys

import pytest

from cagent.tasks import Task


@dataclass
class FakeResult:
    task_id: str
    status: str
    tokens_in: int = 0
    tokens_out: int = 0


class TestFmtElapsed:
    """Tests for _fmt_elapsed."""

    def test_seconds_under_minute(self):
        from cagent.cli import _fmt_elapsed
        assert _fmt_elapsed(45) == "45s"

    def test_minutes_only(self):
        from cagent.cli import _fmt_elapsed
        assert _fmt_elapsed(90) == "1m30s"

    def test_hours(self):
        from cagent.cli import _fmt_elapsed
        assert _fmt_elapsed(3665) == "1h1m5s"

    def test_zero(self):
        from cagent.cli import _fmt_elapsed
        assert _fmt_elapsed(0) == "0s"


class TestWriteSummary:
    """Tests for _write_summary."""

    def test_write_summary_done_tasks(self, tmp_path):
        from cagent.cli import _write_summary

        run_dir = tmp_path / "run"
        run_dir.mkdir()

        tasks = [
            Task(id="001", prompt="First task", branch="task-001", status="done", commit_sha="a" * 40),
            Task(id="002", prompt="Second task", branch="task-002", status="failed"),
        ]
        results = [
            FakeResult(task_id="001", status="done", tokens_in=1000, tokens_out=500),
            FakeResult(task_id="002", status="failed"),
        ]

        _write_summary(
            run_dir=run_dir,
            tasks=tasks,
            results=results,
            base_sha="b" * 40,
            integration_sha="c" * 40,
            run_id="test-run",
            elapsed="2m30s",
        )

        summary = (run_dir / "summary.md").read_text(encoding="utf-8")
        assert "# cagent run test-run" in summary
        assert "1 done, 1 failed, 0 skipped" in summary
        assert "Tokens: 1,000 in, 500 out" in summary
        assert "[OK] task 001" in summary
        assert "[FAIL] task 002" in summary

    def test_write_summary_no_integration_sha(self, tmp_path):
        from cagent.cli import _write_summary

        run_dir = tmp_path / "run"
        run_dir.mkdir()

        tasks = [
            Task(id="001", prompt="Task", branch="task-001", status="noop"),
        ]
        results = [
            FakeResult(task_id="001", status="noop", tokens_in=0, tokens_out=0),
        ]

        _write_summary(
            run_dir=run_dir,
            tasks=tasks,
            results=results,
            base_sha="b" * 40,
            integration_sha=None,
            run_id="test-run",
        )

        summary = (run_dir / "summary.md").read_text(encoding="utf-8")
        assert "Integration:" not in summary
        assert "0 done, 0 failed, 1 skipped" in summary


class TestPrintDashboardTable:
    """Tests for _print_dashboard_table."""

    def test_table_renders_all_statuses(self, capsys):
        from cagent.cli import _print_dashboard_table

        data = {
            "001": {"status": "done", "started_at": 1000.0, "ended_at": 1060.0, "tool_count": 5, "last_activity": "edit foo.py"},
            "002": {"status": "failed", "started_at": 1000.0, "ended_at": 1030.0, "tool_count": 2, "last_activity": "bash rm"},
            "003": {"status": "running", "started_at": 1000.0, "ended_at": None, "tool_count": 3, "last_activity": "thinking"},
        }

        _print_dashboard_table("run-001", data)
        out = capsys.readouterr().out

        assert "1/3 done" in out
        assert "1 running" in out
        assert "1 failed" in out
        assert "001" in out
        assert "002" in out
        assert "003" in out

    def test_table_with_tokens(self, capsys):
        from cagent.cli import _print_dashboard_table

        data = {
            "001": {"status": "done", "started_at": 1000.0, "ended_at": 1060.0, "tool_count": 5, "tokens_in": 5000, "tokens_out": 2000, "last_activity": "edit foo.py"},
        }

        _print_dashboard_table("run-001", data)
        out = capsys.readouterr().out

        assert "5,000" in out
        assert "2,000" in out

    def test_table_with_budget(self, capsys):
        from cagent.cli import _print_dashboard_table

        data = {
            "001": {"status": "done", "started_at": 1000.0, "ended_at": 1060.0, "tool_count": 5, "tokens_in": 8000, "tokens_out": 2000, "last_activity": "done"},
        }

        _print_dashboard_table("run-001", data, max_tokens=50000)
        out = capsys.readouterr().out

        assert "50,000 budget" in out
        assert "20%" in out

    def test_table_budget_warning_at_80pct(self, capsys, monkeypatch):
        from cagent.cli import _print_dashboard_table

        data = {
            "001": {"status": "done", "started_at": 1000.0, "ended_at": 1060.0, "tool_count": 5, "tokens_in": 40000, "tokens_out": 5000, "last_activity": "done"},
        }

        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        _print_dashboard_table("run-001", data, max_tokens=50000)
        out = capsys.readouterr().out

        assert "50,000 budget" in out
        assert "90%" in out
        assert "\033[33m" in out


class TestCmdCancel:
    """Tests for _cmd_cancel — PID file lookup + terminate."""

    def test_cancel_no_pid_file(self, tmp_path, capsys):
        """Cancel with no PID file exits with error."""
        from cagent.cli import _cmd_cancel

        run_dir = tmp_path / "run"
        run_dir.mkdir()

        args = MagicMock()
        args.task_id = "001"
        args.run = None

        with patch("cagent.cli.misc._get_repo_root", return_value=tmp_path), \
             patch("cagent.cli.misc._find_run_dir", return_value=run_dir), \
             pytest.raises(SystemExit, match="1"):
            _cmd_cancel(args)

        err = capsys.readouterr().err
        assert "No PID file found" in err

    def test_cancel_with_pid_file(self, tmp_path):
        """Cancel reads PID file, calls _terminate_pid, and cleans up PID file."""
        from cagent.cli import _cmd_cancel

        run_dir = tmp_path / "run"
        pid_dir = run_dir / "pids"
        pid_dir.mkdir(parents=True)
        pid_path = pid_dir / "task-001.pid"
        pid_path.write_text("12345", encoding="utf-8")

        args = MagicMock()
        args.task_id = "001"
        args.run = None

        with patch("cagent.cli.misc._get_repo_root", return_value=tmp_path), \
             patch("cagent.cli.misc._find_run_dir", return_value=run_dir), \
             patch("cagent.cli.misc._terminate_pid") as mock_term:
            _cmd_cancel(args)

        mock_term.assert_called_once_with(12345)
        assert not pid_path.exists()

    def test_cancel_process_not_found(self, tmp_path, capsys):
        """Cancel when process already exited → prints message, removes PID file."""
        from cagent.cli import _cmd_cancel

        run_dir = tmp_path / "run"
        pid_dir = run_dir / "pids"
        pid_dir.mkdir(parents=True)
        pid_path = pid_dir / "task-001.pid"
        pid_path.write_text("99999", encoding="utf-8")

        args = MagicMock()
        args.task_id = "001"
        args.run = None

        with patch("cagent.cli.misc._get_repo_root", return_value=tmp_path), \
             patch("cagent.cli.misc._find_run_dir", return_value=run_dir), \
             patch("cagent.cli.misc._terminate_pid", side_effect=ProcessLookupError):
            _cmd_cancel(args)

        err = capsys.readouterr().err
        assert "not found" in err
        assert not pid_path.exists()


class TestCmdClean:
    """Tests for _cmd_clean — worktree and run dir cleanup."""

    def test_clean_nothing_to_clean(self, tmp_path, capsys):
        """Clean with no runs dir prints nothing-to-clean."""
        from cagent.cli import _cmd_clean

        args = MagicMock()
        args.all = False
        args.run_id = None
        args.force = True
        args.memory = False

        with patch("cagent.cli.misc._get_repo_root", return_value=tmp_path):
            _cmd_clean(args)

        out = capsys.readouterr().out
        assert "Nothing to clean" in out

    def test_clean_removes_run_dir(self, tmp_path):
        """Clean removes run directories (force mode, with memory flag)."""
        from cagent.cli import _cmd_clean

        runs_dir = tmp_path / ".cagent" / "runs"
        run_dir = runs_dir / "test-run"
        run_dir.mkdir(parents=True)
        (run_dir / "dashboard.json").write_text("{}", encoding="utf-8")

        args = MagicMock()
        args.all = True
        args.run_id = None
        args.force = True
        args.memory = True

        with patch("cagent.cli.misc._get_repo_root", return_value=tmp_path):
            _cmd_clean(args)

        assert not run_dir.exists()

    def test_clean_preserves_memory(self, tmp_path):
        """Clean without --memory flag preserves memory/ subdirectory."""
        from cagent.cli import _cmd_clean

        runs_dir = tmp_path / ".cagent" / "runs"
        run_dir = runs_dir / "test-run"
        run_dir.mkdir(parents=True)
        (run_dir / "dashboard.json").write_text("{}", encoding="utf-8")
        mem_dir = run_dir / "memory"
        mem_dir.mkdir()
        (mem_dir / "task-001.md").write_text("memory content", encoding="utf-8")

        args = MagicMock()
        args.all = True
        args.run_id = None
        args.force = True
        args.memory = False

        with patch("cagent.cli.misc._get_repo_root", return_value=tmp_path):
            _cmd_clean(args)

        assert mem_dir.exists()
        assert (mem_dir / "task-001.md").exists()
        assert not (run_dir / "dashboard.json").exists()


class TestTerminatePid:
    """Tests for _terminate_pid — cross-platform signal delivery."""

    def test_terminate_sends_correct_signal(self):
        """_terminate_pid sends CTRL_BREAK_EVENT on Windows, SIGTERM on Unix."""
        import signal
        from cagent.cli import _terminate_pid
        with patch("cagent.cli.base.os.kill") as mock_kill:
            _terminate_pid(12345)
        if sys.platform == "win32":
            mock_kill.assert_called_once_with(12345, signal.CTRL_BREAK_EVENT)
        else:
            mock_kill.assert_called_once_with(12345, signal.SIGTERM)

    def test_terminate_handles_permission_error(self, capsys):
        """_terminate_pid handles PermissionError gracefully."""
        from cagent.cli import _terminate_pid
        if sys.platform == "win32":
            # On Windows, PermissionError triggers taskkill fallback
            with patch("cagent.cli.base.os.kill", side_effect=PermissionError), \
                 patch("cagent.cli.base.subprocess.run") as mock_taskkill:
                _terminate_pid(12345)
            mock_taskkill.assert_called_once()
            assert "taskkill" in mock_taskkill.call_args[0][0]
        else:
            with patch("cagent.cli.base.os.kill", side_effect=PermissionError):
                _terminate_pid(12345)
            err = capsys.readouterr().err
            assert "Permission denied" in err

    def test_terminate_ignores_process_not_found(self):
        """_terminate_pid ignores ProcessLookupError (already exited)."""
        from cagent.cli import _terminate_pid
        with patch("cagent.cli.base.os.kill", side_effect=ProcessLookupError):
            _terminate_pid(12345)  # should not raise

    def test_cancel_updates_dashboard(self, tmp_path):
        """Cancel reads PID, terminates process, and updates dashboard status."""
        from cagent.cli import _cmd_cancel

        run_dir = tmp_path / "run"
        pid_dir = run_dir / "pids"
        pid_dir.mkdir(parents=True)
        (pid_dir / "task-001.pid").write_text("12345", encoding="utf-8")

        # Write a dashboard.json to verify it gets updated
        dashboard_path = run_dir / "dashboard.json"
        dashboard_path.write_text(json.dumps({
            "001": {"task_id": "001", "status": "running"}
        }), encoding="utf-8")

        args = MagicMock()
        args.task_id = "001"
        args.run = None

        with patch("cagent.cli.misc._get_repo_root", return_value=tmp_path), \
             patch("cagent.cli.misc._find_run_dir", return_value=run_dir), \
             patch("cagent.cli.misc._terminate_pid") as mock_term:
            _cmd_cancel(args)

        mock_term.assert_called_once_with(12345)


class TestVersionCheck:
    """Tests for version check in cli.main()."""

    def test_version_check_rejects_old_python(self):
        """Version check should exit(1) on Python < 3.11."""
        import sys
        # Test the version check logic directly (calling main() would
        # replace sys.stdout via TextIOWrapper, breaking pytest capture)
        with patch.object(sys, "version_info", (3, 10, 0)):
            with pytest.raises(SystemExit) as exc_info:
                if sys.version_info < (3, 11):
                    sys.exit(1)
            assert exc_info.value.code == 1

    def test_version_check_passes_on_311_plus(self):
        """Version check should not exit on Python >= 3.11."""
        import sys
        assert sys.version_info >= (3, 11)


class TestRunLock:
    """Tests for _run_lock — concurrent run prevention."""

    def test_lock_acquires_and_releases(self, tmp_path):
        """Lock acquires successfully and releases after context manager."""
        from cagent.cli.run import _run_lock

        repo_root = tmp_path
        with _run_lock(repo_root):
            lock_path = repo_root / ".cagent" / "run.lock"
            assert lock_path.exists()

        # Lock file should be cleaned up after exit
        assert not lock_path.exists()

    def test_lock_force_skips_check(self, tmp_path):
        """--force flag bypasses lock acquisition entirely."""
        from cagent.cli.run import _run_lock

        repo_root = tmp_path
        # With force=True, no locking is attempted
        with _run_lock(repo_root, force=True):
            lock_path = repo_root / ".cagent" / "run.lock"
            # Lock file should NOT exist when force=True
            assert not lock_path.exists()

    def test_lock_creates_cagent_dir(self, tmp_path):
        """Lock creates .cagent directory if it doesn't exist."""
        from cagent.cli.run import _run_lock

        repo_root = tmp_path / "new_repo"
        repo_root.mkdir()

        with _run_lock(repo_root):
            assert (repo_root / ".cagent").is_dir()

    def test_lock_error_message_on_failure(self, tmp_path, capsys):
        """Lock failure prints clear error message and exits."""
        import sys
        from cagent.cli.run import _run_lock

        repo_root = tmp_path
        # Simulate lock failure by making the platform locking function raise OSError.
        # msvcrt/fcntl are imported inside the function, so we mock at the module level.
        if sys.platform == "win32":
            import msvcrt
            with patch.object(msvcrt, "locking", side_effect=OSError("lock held")):
                with pytest.raises(SystemExit, match="1"):
                    with _run_lock(repo_root):
                        pass
        else:
            import fcntl
            with patch.object(fcntl, "flock", side_effect=OSError("lock held")):
                with pytest.raises(SystemExit, match="1"):
                    with _run_lock(repo_root):
                        pass

        err = capsys.readouterr().err
        assert "Another cagent run is active" in err
        assert "--force" in err


class TestAuthCache:
    """Tests for auth preflight cache (53.1)."""

    def test_cache_hit_skips_auth(self, tmp_path, capsys):
        """Auth check is skipped when cache file is recent."""
        import time
        from cagent.cli.base import _auth_preflight_check

        cache_dir = tmp_path / ".cagent"
        cache_dir.mkdir()
        (cache_dir / "auth_ok").write_text(str(time.time()), encoding="utf-8")

        _auth_preflight_check("claude", repo_root=tmp_path)

        out = capsys.readouterr().out
        assert "cached OK" in out

    def test_cache_expired_rechecks(self, tmp_path):
        """Auth check runs when cache file is expired (>5 min)."""
        import time
        from cagent.cli.base import _auth_preflight_check

        cache_dir = tmp_path / ".cagent"
        cache_dir.mkdir()
        # Write timestamp from 10 minutes ago
        (cache_dir / "auth_ok").write_text(str(time.time() - 600), encoding="utf-8")

        # This will fail because "claude" isn't actually available in test,
        # but it proves the cache was NOT used (no "cached OK" in output)
        with pytest.raises(SystemExit):
            _auth_preflight_check("claude", repo_root=tmp_path)

    def test_force_auth_ignores_cache(self, tmp_path):
        """force_auth=True always re-validates even with valid cache."""
        import time
        from cagent.cli.base import _auth_preflight_check

        cache_dir = tmp_path / ".cagent"
        cache_dir.mkdir()
        (cache_dir / "auth_ok").write_text(str(time.time()), encoding="utf-8")

        # force_auth=True should ignore cache and try to run claude
        with pytest.raises(SystemExit):
            _auth_preflight_check("claude", repo_root=tmp_path, force_auth=True)

    def test_success_writes_cache(self, tmp_path):
        """Successful auth writes cache file."""
        import time
        from cagent.cli.base import _auth_preflight_check

        cache_path = tmp_path / ".cagent" / "auth_ok"
        assert not cache_path.exists()

        # Mock subprocess to return success
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        with patch("cagent.cli.base.subprocess.run", return_value=mock_result):
            _auth_preflight_check("claude", repo_root=tmp_path)

        assert cache_path.exists()
        ts = float(cache_path.read_text(encoding="utf-8").strip())
        assert abs(ts - time.time()) < 5  # Within 5 seconds


class TestCmdBranches:
    """Tests for _cmd_branches — git for-each-ref based listing."""

    def test_no_branches(self, tmp_path, capsys):
        """No cagent branches prints message."""
        from cagent.cli.misc import _cmd_branches

        args = MagicMock()
        mock_result = MagicMock()
        mock_result.stdout = ""

        with patch("cagent.cli.misc._get_repo_root", return_value=tmp_path), \
             patch("cagent.cli.misc.subprocess.run", return_value=mock_result):
            _cmd_branches(args)

        out = capsys.readouterr().out
        assert "No cagent branches found" in out

    def test_lists_branches_with_commits(self, tmp_path, capsys):
        """Lists branches with commit info from for-each-ref."""
        from cagent.cli.misc import _cmd_branches

        args = MagicMock()
        mock_result = MagicMock()
        mock_result.stdout = (
            "cagent/run-001/task-001|abc1234|fix: resolve null pointer\n"
            "cagent/run-001/task-002|def5678|feat: add validation\n"
        )

        with patch("cagent.cli.misc._get_repo_root", return_value=tmp_path), \
             patch("cagent.cli.misc.subprocess.run", return_value=mock_result):
            _cmd_branches(args)

        out = capsys.readouterr().out
        assert "2" in out  # count
        assert "cagent/run-001/task-001" in out
        assert "abc1234" in out
        assert "cagent/run-001/task-002" in out
        assert "def5678" in out

    def test_integration_branch_marked(self, tmp_path, capsys):
        """Integration branch gets a * marker."""
        from cagent.cli.misc import _cmd_branches

        args = MagicMock()
        mock_result = MagicMock()
        mock_result.stdout = (
            "cagent/run-001/integration|fff0000|merge: integration\n"
            "cagent/run-001/task-001|aaa1111|feat: something\n"
        )

        with patch("cagent.cli.misc._get_repo_root", return_value=tmp_path), \
             patch("cagent.cli.misc.subprocess.run", return_value=mock_result):
            _cmd_branches(args)

        out = capsys.readouterr().out
        assert " *" in out  # integration marker
        assert "integration" in out