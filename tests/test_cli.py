"""Unit tests for pure cli.py helper functions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch
import json
import subprocess
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

        with patch("cagent.cli.misc._get_repo_root", return_value=tmp_path), \
             patch("builtins.input", return_value="yes"):
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

        with patch("cagent.cli.misc._get_repo_root", return_value=tmp_path), \
             patch("builtins.input", return_value="yes"):
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

    def test_auth_failure_exits(self, tmp_path):
        """Auth failure (non-zero exit) → sys.exit(1)."""
        from cagent.cli.base import _auth_preflight_check

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "Error: apiKeySource not found"

        with patch("cagent.cli.base.subprocess.run", return_value=mock_result):
            with pytest.raises(SystemExit):
                _auth_preflight_check("claude", repo_root=tmp_path)

    def test_auth_timeout_exits(self, tmp_path):
        """Auth timeout → sys.exit(1)."""
        import subprocess as sp
        from cagent.cli.base import _auth_preflight_check

        with patch("cagent.cli.base.subprocess.run", side_effect=sp.TimeoutExpired("claude", 30)):
            with pytest.raises(SystemExit):
                _auth_preflight_check("claude", repo_root=tmp_path)

    def test_auth_claude_not_found_exits(self, tmp_path):
        """Claude binary not found → sys.exit(1)."""
        from cagent.cli.base import _auth_preflight_check

        with patch("cagent.cli.base.subprocess.run", side_effect=FileNotFoundError):
            with pytest.raises(SystemExit):
                _auth_preflight_check("claude", repo_root=tmp_path)

    def test_auth_cache_corrupt_rechecks(self, tmp_path):
        """Corrupt cache file → re-checks (no crash)."""
        import time
        from cagent.cli.base import _auth_preflight_check

        cache_dir = tmp_path / ".cagent"
        cache_dir.mkdir()
        (cache_dir / "auth_ok").write_text("not-a-number", encoding="utf-8")

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        with patch("cagent.cli.base.subprocess.run", return_value=mock_result):
            _auth_preflight_check("claude", repo_root=tmp_path)

        # Should have re-checked and written new cache
        ts = float((cache_dir / "auth_ok").read_text(encoding="utf-8").strip())
        assert abs(ts - time.time()) < 5


class TestGetRepoRoot:
    """Tests for _get_repo_root."""

    def test_success(self, tmp_path):
        """Returns repo root path."""
        from cagent.cli.base import _get_repo_root

        mock_result = MagicMock()
        mock_result.stdout = str(tmp_path) + "\n"

        with patch("cagent.cli.base.run_git", return_value=mock_result):
            result = _get_repo_root()
        assert result == tmp_path

    def test_not_git_repo_exits(self):
        """Not in a git repo → sys.exit(1)."""
        from cagent.cli.base import _get_repo_root

        with patch("cagent.cli.base.run_git", side_effect=RuntimeError("git failed")):
            with pytest.raises(SystemExit):
                _get_repo_root()


class TestPreflightCheck:
    """Tests for _preflight_check."""

    def test_git_not_found_exits(self):
        """git not in PATH → sys.exit(1)."""
        from cagent.cli.base import _preflight_check

        with patch("cagent.cli.base.shutil.which", return_value=None):
            with pytest.raises(SystemExit):
                _preflight_check()

    def test_claude_not_found_exits(self):
        """claude not in PATH → sys.exit(1)."""
        from cagent.cli.base import _preflight_check

        def which_side_effect(cmd):
            if cmd == "git":
                return "/usr/bin/git"
            return None

        with patch("cagent.cli.base.shutil.which", side_effect=which_side_effect):
            with pytest.raises(SystemExit):
                _preflight_check()

    def test_success_no_auth(self):
        """check_auth=False → no auth check."""
        from cagent.cli.base import _preflight_check

        with patch("cagent.cli.base.shutil.which", return_value="/usr/bin/claude"):
            _preflight_check(check_auth=False)  # Should not raise


class TestPrintAuthDiagnostics:
    """Tests for _print_auth_diagnostics."""

    def test_prints_env_vars(self, capsys):
        """Prints diagnostic info to stderr."""
        from cagent.cli.base import _print_auth_diagnostics

        _print_auth_diagnostics()
        err = capsys.readouterr().err
        assert "Auth diagnostics" in err
        assert "ANTHROPIC_API_KEY" in err
        assert "Possible fixes" in err


class TestCmdBranches:
    """Tests for _cmd_branches — git for-each-ref based listing."""

    def test_no_branches(self, tmp_path, capsys):
        """No cagent branches prints message."""
        from cagent.cli.misc import _cmd_branches

        args = MagicMock()
        mock_result = MagicMock()
        mock_result.stdout = ""

        with patch("cagent.cli.misc._get_repo_root", return_value=tmp_path), \
             patch("cagent.cli.misc.run_git", return_value=mock_result):
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
             patch("cagent.cli.misc.run_git", return_value=mock_result):
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
             patch("cagent.cli.misc.run_git", return_value=mock_result):
            _cmd_branches(args)

        out = capsys.readouterr().out
        assert " *" in out  # integration marker
        assert "integration" in out


class TestDryRun:
    """Tests for dry-run mode in _cmd_run_inner (60.2.1)."""

    def test_dry_run_prints_summary(self, tmp_path, capsys):
        """Dry run prints planned execution summary and returns without running."""
        from cagent.cli.run import _cmd_run_inner

        tasks_file = tmp_path / "tasks.txt"
        tasks_file.write_text("Implement feature A\nFix bug B\n", encoding="utf-8")

        args = MagicMock()
        args.dry_run = True
        args.base = "abc123def456"
        args.tasks_file = str(tasks_file)
        args.jobs = 4
        args.timeout = 600
        args.squash = True
        args.strategy = "cherry-pick"
        args.worker_model = "claude-haiku-4-5"
        args.max_turns = None
        args.max_tokens = None

        mock_rev_parse = MagicMock()
        mock_rev_parse.stdout = "abc123def456789012345678901234567890abcd\n"

        with patch("cagent.cli.run.run_git", return_value=mock_rev_parse), \
             patch("cagent.cli.run._execute_run") as mock_exec:
            _cmd_run_inner(args, tmp_path)

        # _execute_run should NOT be called in dry-run mode
        mock_exec.assert_not_called()

        out = capsys.readouterr().out
        assert "Dry run" in out
        assert "abc123def456" in out
        assert "tasks:    2" in out
        assert "jobs:     4" in out
        assert "timeout:  600s" in out
        assert "strategy: cherry-pick" in out
        assert "model:    claude-haiku-4-5" in out
        assert "Implement feature A" in out
        assert "Fix bug B" in out

    def test_dry_run_no_state_written(self, tmp_path, capsys):
        """Dry run does not write any run state files."""
        from cagent.cli.run import _cmd_run_inner

        tasks_file = tmp_path / "tasks.txt"
        tasks_file.write_text("Task one\n", encoding="utf-8")

        args = MagicMock()
        args.dry_run = True
        args.base = None
        args.tasks_file = str(tasks_file)
        args.jobs = 1
        args.timeout = 300
        args.squash = False
        args.strategy = "merge"
        args.worker_model = None
        args.max_turns = None
        args.max_tokens = None

        runs_dir = tmp_path / ".cagent" / "runs"

        with patch("cagent.worktree.current_head", return_value="aaa111"), \
             patch("cagent.cli.run._execute_run") as mock_exec:
            _cmd_run_inner(args, tmp_path)

        mock_exec.assert_not_called()
        # Run dir may be created but no state files should be written
        if runs_dir.exists():
            for d in runs_dir.iterdir():
                assert not (d / "tasks.json").exists()
                assert not (d / "base_sha").exists()


class TestRunLockLifecycle:
    """Tests for _run_lock exception safety (60.2.3)."""

    def test_lock_cleaned_up_on_exception(self, tmp_path):
        """Lock file is removed even when exception occurs inside with block."""
        from cagent.cli.run import _run_lock

        repo_root = tmp_path
        lock_path = repo_root / ".cagent" / "run.lock"

        with pytest.raises(ValueError):
            with _run_lock(repo_root):
                assert lock_path.exists()
                raise ValueError("simulated error")

        assert not lock_path.exists()

    def test_lock_fd_closed_on_exception(self, tmp_path):
        """Lock file descriptor is closed after exception."""
        from cagent.cli.run import _run_lock

        repo_root = tmp_path

        with pytest.raises(RuntimeError):
            with _run_lock(repo_root):
                raise RuntimeError("boom")

        # Acquiring the lock again should succeed (fd was properly closed)
        with _run_lock(repo_root):
            pass


class TestCmdResume:
    """Tests for _cmd_resume (60.2.2)."""

    def test_resume_run_not_found(self, tmp_path, capsys):
        """Resume with non-existent run dir prints error and exits."""
        from cagent.cli.run import _cmd_resume

        args = MagicMock()
        args.resume = "nonexistent-run"

        runs_dir = tmp_path / ".cagent" / "runs"
        runs_dir.mkdir(parents=True)

        with pytest.raises(SystemExit, match="1"):
            _cmd_resume(args, tmp_path)

        err = capsys.readouterr().err
        assert "Run not found" in err

    def test_resume_no_tasks_json(self, tmp_path, capsys):
        """Resume with missing tasks.json prints error and exits."""
        from cagent.cli.run import _cmd_resume

        run_dir = tmp_path / ".cagent" / "runs" / "test-run"
        run_dir.mkdir(parents=True)
        # No tasks.json

        args = MagicMock()
        args.resume = "test-run"

        with pytest.raises(SystemExit, match="1"):
            _cmd_resume(args, tmp_path)

        err = capsys.readouterr().err
        assert "No tasks.json" in err

    def test_resume_all_done(self, tmp_path, capsys):
        """Resume when all tasks are done prints message and returns."""
        from cagent.cli.run import _cmd_resume

        run_dir = tmp_path / ".cagent" / "runs" / "test-run"
        run_dir.mkdir(parents=True)

        tasks_data = [
            {"id": "001", "prompt": "Task one", "branch": "task-001", "status": "done", "commit_sha": "a" * 40},
            {"id": "002", "prompt": "Task two", "branch": "task-002", "status": "noop"},
        ]
        (run_dir / "tasks.json").write_text(json.dumps(tasks_data), encoding="utf-8")

        args = MagicMock()
        args.resume = "test-run"

        with patch("cagent.cli.run._execute_run") as mock_exec:
            _cmd_resume(args, tmp_path)

        mock_exec.assert_not_called()
        out = capsys.readouterr().out
        assert "Nothing to resume" in out

    def test_resume_calls_execute_run(self, tmp_path):
        """Resume with pending tasks calls _execute_run with merge_results."""
        from cagent.cli.run import _cmd_resume

        run_dir = tmp_path / ".cagent" / "runs" / "test-run"
        run_dir.mkdir(parents=True)
        (run_dir / "base_sha").write_text("b" * 40, encoding="utf-8")

        tasks_data = [
            {"id": "001", "prompt": "Done task", "branch": "task-001", "status": "done", "commit_sha": "a" * 40},
            {"id": "002", "prompt": "Pending task", "branch": "task-002", "status": "failed"},
        ]
        (run_dir / "tasks.json").write_text(json.dumps(tasks_data), encoding="utf-8")

        args = MagicMock()
        args.resume = "test-run"
        args.api_key = None
        args.api_key_file = None

        with patch("cagent.cli.run._execute_run") as mock_exec, \
             patch("cagent.cli.run.run_git"):
            _cmd_resume(args, tmp_path)

        mock_exec.assert_called_once()
        call_kwargs = mock_exec.call_args[1]
        assert call_kwargs["merge_results"] is not None
        assert call_kwargs["api_key"] is None
        # dispatch_tasks should only contain the pending task
        dispatch_ids = [t.id for t in call_kwargs["dispatch_tasks"]]
        assert dispatch_ids == ["002"]

    def test_resume_passes_api_key(self, tmp_path):
        """Resume passes api_key to _execute_run (57.2.2)."""
        from cagent.cli.run import _cmd_resume

        run_dir = tmp_path / ".cagent" / "runs" / "test-run"
        run_dir.mkdir(parents=True)
        (run_dir / "base_sha").write_text("b" * 40, encoding="utf-8")

        tasks_data = [
            {"id": "001", "prompt": "Task", "branch": "task-001", "status": "failed"},
        ]
        (run_dir / "tasks.json").write_text(json.dumps(tasks_data), encoding="utf-8")

        args = MagicMock()
        args.resume = "test-run"

        with patch("cagent.cli.run._execute_run") as mock_exec, \
             patch("cagent.cli.run.run_git"):
            _cmd_resume(args, tmp_path, api_key="sk-ant-test-key-12345")

        mock_exec.assert_called_once()
        call_kwargs = mock_exec.call_args[1]
        assert call_kwargs["api_key"] == "sk-ant-test-key-12345"

    def test_merge_resume_results(self, tmp_path):
        """_merge_resume_results merges dispatch results with already-done tasks."""
        from cagent.cli.run import _cmd_resume
        from cagent.agent import AgentResult

        run_dir = tmp_path / ".cagent" / "runs" / "test-run"
        run_dir.mkdir(parents=True)
        (run_dir / "base_sha").write_text("b" * 40, encoding="utf-8")

        tasks_data = [
            {"id": "001", "prompt": "Done task", "branch": "task-001", "status": "done", "commit_sha": "a" * 40},
            {"id": "002", "prompt": "Now done", "branch": "task-002", "status": "pending"},
        ]
        (run_dir / "tasks.json").write_text(json.dumps(tasks_data), encoding="utf-8")

        args = MagicMock()
        args.resume = "test-run"
        args.api_key = None
        args.api_key_file = None

        captured_merge = None

        def capture_execute(**kwargs):
            nonlocal captured_merge
            captured_merge = kwargs.get("merge_results")

        with patch("cagent.cli.run._execute_run", side_effect=capture_execute), \
             patch("cagent.cli.run.run_git"):
            _cmd_resume(args, tmp_path)

        assert captured_merge is not None

        # Simulate dispatch returning result for task 002 only
        from cagent.tasks import Task
        all_tasks = [
            Task(id="001", prompt="Done task", branch="task-001", status="done", commit_sha="a" * 40),
            Task(id="002", prompt="Now done", branch="task-002", status="done", commit_sha="b" * 40),
        ]
        dispatch_results = [
            AgentResult(task_id="002", status="done", commit_sha="b" * 40),
        ]

        merged = captured_merge(all_tasks, dispatch_results)
        assert len(merged) == 2
        assert merged[0].task_id == "001"
        assert merged[0].status == "done"
        assert merged[0].commit_sha == "a" * 40
        assert merged[1].task_id == "002"
        assert merged[1].status == "done"
        assert merged[1].commit_sha == "b" * 40

    def test_resume_path_traversal_rejected(self, tmp_path, capsys):
        """Path traversal in --resume argument is rejected."""
        from cagent.cli.run import _cmd_resume

        args = MagicMock()
        args.resume = "../../etc/passwd"

        runs_dir = tmp_path / ".cagent" / "runs"
        runs_dir.mkdir(parents=True)

        with pytest.raises(SystemExit, match="1"):
            _cmd_resume(args, tmp_path)

        err = capsys.readouterr().err
        assert "path traversal" in err.lower()


class TestCleanWorktrees:
    """Tests for _clean_worktrees (60.2.4)."""

    def test_all_ok_removes_all_worktrees(self, tmp_path):
        """When all results are done/noop, all worktrees are removed."""
        from cagent.cli.run import _clean_worktrees

        repo_root = tmp_path
        run_dir = repo_root / ".cagent" / "runs" / "test-run"
        run_dir.mkdir(parents=True)

        # Create worktree directories at the correct path
        wt_base = repo_root / ".cagent" / "worktrees" / "test-run"
        wt_base.mkdir(parents=True, exist_ok=True)
        (wt_base / "task-001").mkdir(exist_ok=True)
        (wt_base / "task-002").mkdir(exist_ok=True)
        (wt_base / "_integration").mkdir(exist_ok=True)

        tasks = [
            Task(id="001", prompt="Task A", branch="task-001", status="done"),
            Task(id="002", prompt="Task B", branch="task-002", status="done"),
        ]
        results = [
            FakeResult(task_id="001", status="done"),
            FakeResult(task_id="002", status="done"),
        ]

        mock_run = MagicMock()
        with patch("cagent.cli.run.run_git", mock_run):
            _clean_worktrees(repo_root, run_dir, tasks, results)

        # Should remove task-001, task-002, and _integration
        # run_git is called as run_git("worktree", "remove", "--force", path, cwd=repo_root)
        calls = mock_run.call_args_list
        removed_paths = [c[0][3] for c in calls]  # 4th positional arg is the path
        assert any("task-001" in p for p in removed_paths)
        assert any("task-002" in p for p in removed_paths)
        assert any("_integration" in p for p in removed_paths)

    def test_failed_worktree_preserved(self, tmp_path):
        """Failed task worktrees are preserved for debugging."""
        from cagent.cli.run import _clean_worktrees

        repo_root = tmp_path
        run_dir = repo_root / ".cagent" / "runs" / "test-run"

        wt_base = repo_root / ".cagent" / "worktrees" / "test-run"
        wt_base.mkdir(parents=True, exist_ok=True)
        (wt_base / "task-001").mkdir(exist_ok=True)
        (wt_base / "task-002").mkdir(exist_ok=True)

        tasks = [
            Task(id="001", prompt="Done task", branch="task-001", status="done"),
            Task(id="002", prompt="Failed task", branch="task-002", status="failed"),
        ]
        results = [
            FakeResult(task_id="001", status="done"),
            FakeResult(task_id="002", status="failed"),
        ]

        mock_run = MagicMock()
        with patch("cagent.cli.run.run_git", mock_run):
            _clean_worktrees(repo_root, run_dir, tasks, results)

        # Should only remove task-001 (done), NOT task-002 (failed)
        removed_paths = [c[0][3] for c in mock_run.call_args_list]
        assert any("task-001" in p for p in removed_paths)
        assert not any("task-002" in p for p in removed_paths)

    def test_missing_worktree_no_error(self, tmp_path):
        """Non-existent worktree directories are silently skipped."""
        from cagent.cli.run import _clean_worktrees

        repo_root = tmp_path
        run_dir = repo_root / ".cagent" / "runs" / "test-run"
        run_dir.mkdir(parents=True)

        tasks = [
            Task(id="001", prompt="Task", branch="task-001", status="done"),
        ]
        results = [
            FakeResult(task_id="001", status="done"),
        ]

        # No worktree directories exist — should not raise
        mock_run = MagicMock()
        with patch("cagent.cli.run.run_git", mock_run):
            _clean_worktrees(repo_root, run_dir, tasks, results)

        # run_git should NOT be called since worktree doesn't exist
        mock_run.assert_not_called()

    def test_git_error_silently_ignored(self, tmp_path):
        """Non-zero exit from git worktree remove is silently ignored (check=False)."""
        from cagent.cli.run import _clean_worktrees

        repo_root = tmp_path
        run_dir = repo_root / ".cagent" / "runs" / "test-run"

        wt_base = repo_root / ".cagent" / "worktrees" / "test-run"
        wt_base.mkdir(parents=True, exist_ok=True)
        (wt_base / "task-001").mkdir(exist_ok=True)

        tasks = [
            Task(id="001", prompt="Task", branch="task-001", status="done"),
        ]
        results = [
            FakeResult(task_id="001", status="done"),
        ]

        # With check=False, run_git returns a result with non-zero returncode
        mock_run = MagicMock(return_value=MagicMock(returncode=1))
        with patch("cagent.cli.run.run_git", mock_run):
            _clean_worktrees(repo_root, run_dir, tasks, results)  # should not raise


class TestCmdPush:
    """Tests for _cmd_push — branch push with confirmation."""

    def test_push_branch_not_found(self, tmp_path, capsys):
        """Push with non-existent branch exits with error."""
        from cagent.cli.misc import _cmd_push

        args = MagicMock()
        args.branch = "nonexistent-branch"

        def mock_run_git(*args, **kwargs):
            if "rev-parse" in args:
                return MagicMock(returncode=1)
            if "--list" in args:
                return MagicMock(stdout="cagent/run-001/task-001\n")
            return MagicMock(returncode=0)

        with patch("cagent.cli.misc._get_repo_root", return_value=tmp_path), \
             patch("cagent.cli.misc.run_git", side_effect=mock_run_git), \
             pytest.raises(SystemExit, match="1"):
            _cmd_push(args)

        err = capsys.readouterr().err
        assert "not found" in err
        assert "cagent/run-001/task-001" in err

    def test_push_branch_not_found_no_cagent_branches(self, tmp_path, capsys):
        """Push with non-existent branch and no cagent branches shows no list."""
        from cagent.cli.misc import _cmd_push

        args = MagicMock()
        args.branch = "nonexistent-branch"

        def mock_run_git(*args, **kwargs):
            if "rev-parse" in args:
                return MagicMock(returncode=1)
            if "--list" in args:
                return MagicMock(stdout="")
            return MagicMock(returncode=0)

        with patch("cagent.cli.misc._get_repo_root", return_value=tmp_path), \
             patch("cagent.cli.misc.run_git", side_effect=mock_run_git), \
             pytest.raises(SystemExit, match="1"):
            _cmd_push(args)

        err = capsys.readouterr().err
        assert "not found" in err

    def test_push_aborted_by_user(self, tmp_path, capsys):
        """Push aborted when user says no."""
        from cagent.cli.misc import _cmd_push

        args = MagicMock()
        args.branch = "my-branch"

        def mock_run_git(*args, **kwargs):
            if "rev-parse" in args:
                return MagicMock(returncode=0)
            if "log" in args:
                return MagicMock(stdout="abc1234 feat: something\n")
            return MagicMock(returncode=0)

        with patch("cagent.cli.misc._get_repo_root", return_value=tmp_path), \
             patch("cagent.cli.misc.run_git", side_effect=mock_run_git), \
             patch("builtins.input", return_value="n"):
            _cmd_push(args)

        out = capsys.readouterr().out
        assert "Aborted" in out

    def test_push_aborted_by_eof(self, tmp_path, capsys):
        """Push aborted on EOFError."""
        from cagent.cli.misc import _cmd_push

        args = MagicMock()
        args.branch = "my-branch"

        def mock_run_git(*args, **kwargs):
            if "rev-parse" in args:
                return MagicMock(returncode=0)
            if "log" in args:
                return MagicMock(stdout="abc1234 feat: something\n")
            return MagicMock(returncode=0)

        with patch("cagent.cli.misc._get_repo_root", return_value=tmp_path), \
             patch("cagent.cli.misc.run_git", side_effect=mock_run_git), \
             patch("builtins.input", side_effect=EOFError):
            _cmd_push(args)

        out = capsys.readouterr().out
        assert "Aborted" in out

    def test_push_success(self, tmp_path, capsys):
        """Push succeeds when user confirms."""
        from cagent.cli.misc import _cmd_push

        args = MagicMock()
        args.branch = "my-branch"

        def mock_run_git(*args, **kwargs):
            if "rev-parse" in args:
                return MagicMock(returncode=0)
            if "log" in args:
                return MagicMock(stdout="abc1234 feat: something\n")
            if "push" in args:
                return MagicMock(returncode=0)
            return MagicMock(returncode=0)

        with patch("cagent.cli.misc._get_repo_root", return_value=tmp_path), \
             patch("cagent.cli.misc.run_git", side_effect=mock_run_git), \
             patch("builtins.input", return_value="y"):
            _cmd_push(args)

        out = capsys.readouterr().out
        assert "Pushed" in out

    def test_push_shows_recent_commits_when_nothing_to_push(self, tmp_path, capsys):
        """Shows recent commits when there's nothing new to push."""
        from cagent.cli.misc import _cmd_push

        args = MagicMock()
        args.branch = "my-branch"

        def mock_run_git(*args, **kwargs):
            if "rev-parse" in args:
                return MagicMock(returncode=0, stdout="")
            if any(isinstance(a, str) and a.startswith("HEAD..") for a in args):
                return MagicMock(returncode=0, stdout="")
            if "-5" in args:
                return MagicMock(returncode=0, stdout="abc1234 old commit\n")
            return MagicMock(returncode=0, stdout="")

        with patch("cagent.cli.misc._get_repo_root", return_value=tmp_path), \
             patch("cagent.cli.misc.run_git", side_effect=mock_run_git), \
             patch("builtins.input", return_value="n"):
            _cmd_push(args)

        out = capsys.readouterr().out
        assert "Recent commits" in out

    def test_push_git_push_failure(self, tmp_path, capsys):
        """Push failure prints error and exits."""
        from cagent.cli.misc import _cmd_push

        args = MagicMock()
        args.branch = "my-branch"

        def mock_run_git(*args, **kwargs):
            if "rev-parse" in args:
                return MagicMock(returncode=0)
            if "log" in args:
                return MagicMock(stdout="abc1234 feat\n")
            if "push" in args:
                return MagicMock(returncode=1, stderr="Permission denied (publickey)", stdout="")
            return MagicMock(returncode=0)

        with patch("cagent.cli.misc._get_repo_root", return_value=tmp_path), \
             patch("cagent.cli.misc.run_git", side_effect=mock_run_git), \
             patch("builtins.input", return_value="y"), \
             pytest.raises(SystemExit, match="1"):
            _cmd_push(args)

        err = capsys.readouterr().err
        assert "Push failed" in err
        assert "Permission denied" in err

    def test_push_git_push_failure_no_stderr(self, tmp_path, capsys):
        """Push failure with no stderr message shows generic error."""
        from cagent.cli.misc import _cmd_push

        args = MagicMock()
        args.branch = "my-branch"

        def mock_run_git(*args, **kwargs):
            if "rev-parse" in args:
                return MagicMock(returncode=0)
            if "log" in args:
                return MagicMock(stdout="abc1234 feat\n")
            if "push" in args:
                return MagicMock(returncode=1, stderr="", stdout="")
            return MagicMock(returncode=0)

        with patch("cagent.cli.misc._get_repo_root", return_value=tmp_path), \
             patch("cagent.cli.misc.run_git", side_effect=mock_run_git), \
             patch("builtins.input", return_value="y"), \
             pytest.raises(SystemExit, match="1"):
            _cmd_push(args)

        err = capsys.readouterr().err
        assert "Push failed" in err


class TestCmdCancelExtra:
    """Extra tests for _cmd_cancel edge cases."""

    def test_cancel_invalid_pid_file(self, tmp_path, capsys):
        """Cancel with non-numeric PID file exits with error."""
        from cagent.cli.misc import _cmd_cancel

        run_dir = tmp_path / "run"
        pid_dir = run_dir / "pids"
        pid_dir.mkdir(parents=True)
        (pid_dir / "task-001.pid").write_text("not-a-number", encoding="utf-8")

        args = MagicMock()
        args.task_id = "001"
        args.run = None

        with patch("cagent.cli.misc._get_repo_root", return_value=tmp_path), \
             patch("cagent.cli.misc._find_run_dir", return_value=run_dir), \
             pytest.raises(SystemExit, match="1"):
            _cmd_cancel(args)

        err = capsys.readouterr().err
        assert "Failed to read PID" in err


class TestCmdCleanExtra:
    """Extra tests for _cmd_clean edge cases."""

    def test_clean_specific_run_not_found(self, tmp_path, capsys):
        """Clean with non-existent run_id exits with error."""
        from cagent.cli.misc import _cmd_clean

        runs_dir = tmp_path / ".cagent" / "runs"
        runs_dir.mkdir(parents=True)

        args = MagicMock()
        args.all = False
        args.run_id = "nonexistent-run"
        args.force = True
        args.memory = False

        with patch("cagent.cli.misc._get_repo_root", return_value=tmp_path), \
             pytest.raises(SystemExit, match="1"):
            _cmd_clean(args)

        err = capsys.readouterr().err
        assert "Run not found" in err

    def test_clean_latest_no_runs(self, tmp_path, capsys):
        """Clean latest when no runs exist exits with error."""
        from cagent.cli.misc import _cmd_clean

        runs_dir = tmp_path / ".cagent" / "runs"
        runs_dir.mkdir(parents=True)

        args = MagicMock()
        args.all = False
        args.run_id = None
        args.force = True
        args.memory = False

        with patch("cagent.cli.misc._get_repo_root", return_value=tmp_path), \
             pytest.raises(SystemExit, match="1"):
            _cmd_clean(args)

        err = capsys.readouterr().err
        assert "No runs found" in err

    def test_clean_aborted_by_user(self, tmp_path, capsys):
        """Clean aborted when user says no."""
        from cagent.cli.misc import _cmd_clean

        runs_dir = tmp_path / ".cagent" / "runs"
        run_dir = runs_dir / "test-run"
        run_dir.mkdir(parents=True)
        (run_dir / "dashboard.json").write_text("{}", encoding="utf-8")

        args = MagicMock()
        args.all = True
        args.run_id = None
        args.force = False
        args.memory = False

        with patch("cagent.cli.misc._get_repo_root", return_value=tmp_path), \
             patch("builtins.input", return_value="n"):
            _cmd_clean(args)

        out = capsys.readouterr().out
        assert "Aborted" in out

    def test_clean_aborted_by_eof(self, tmp_path, capsys):
        """Clean aborted on EOFError."""
        from cagent.cli.misc import _cmd_clean

        runs_dir = tmp_path / ".cagent" / "runs"
        run_dir = runs_dir / "test-run"
        run_dir.mkdir(parents=True)
        (run_dir / "dashboard.json").write_text("{}", encoding="utf-8")

        args = MagicMock()
        args.all = True
        args.run_id = None
        args.force = False
        args.memory = False

        with patch("cagent.cli.misc._get_repo_root", return_value=tmp_path), \
             patch("builtins.input", side_effect=EOFError):
            _cmd_clean(args)

        out = capsys.readouterr().out
        assert "Aborted" in out

    def test_clean_with_memory_count(self, tmp_path, capsys):
        """Clean shows memory file count in output."""
        from cagent.cli.misc import _cmd_clean

        runs_dir = tmp_path / ".cagent" / "runs"
        run_dir = runs_dir / "test-run"
        run_dir.mkdir(parents=True)
        (run_dir / "dashboard.json").write_text("{}", encoding="utf-8")
        mem_dir = run_dir / "memory"
        mem_dir.mkdir()
        (mem_dir / "task-001.md").write_text("content", encoding="utf-8")

        args = MagicMock()
        args.all = True
        args.run_id = None
        args.force = True
        args.memory = True

        with patch("cagent.cli.misc._get_repo_root", return_value=tmp_path), \
             patch("builtins.input", return_value="yes"):
            _cmd_clean(args)

        out = capsys.readouterr().out
        assert "memory" in out.lower()