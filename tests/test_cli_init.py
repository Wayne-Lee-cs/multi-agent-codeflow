"""Tests for cagent/cli/__init__.py - lazy imports, _get_version, main routing."""

from __future__ import annotations

import sys
from unittest.mock import patch, MagicMock

import pytest

from cagent.cli import _LAZY_IMPORTS


class TestLazyImports:
    """Tests for __getattr__ lazy import mechanism."""

    def test_known_attribute_resolved(self) -> None:
        """Known lazy attributes should be importable."""
        from cagent.cli import _fmt_elapsed
        assert callable(_fmt_elapsed)

    def test_unknown_attribute_raises(self) -> None:
        """Unknown attributes should raise AttributeError."""
        import cagent.cli
        with pytest.raises(AttributeError, match="no attribute"):
            _ = cagent.cli.nonexistent_function

    def test_all_lazy_imports_listed(self) -> None:
        """All lazy imports should be in _LAZY_IMPORTS dict."""
        expected = {
            "_fmt_elapsed", "_get_repo_root", "_find_run_dir",
            "_terminate_pid", "_write_summary", "_print_dashboard_table",
            "_cmd_cancel", "_cmd_clean",
        }
        assert set(_LAZY_IMPORTS.keys()) == expected


class TestGetVersion:
    """Tests for _get_version helper."""

    def test_version_from_importlib_metadata(self) -> None:
        """Returns version from importlib.metadata when available."""
        from cagent.cli import _get_version

        with patch("importlib.metadata.version", return_value="8.0.0"):
            assert _get_version() == "8.0.0"

    def test_version_fallback_to_pyproject_toml(self) -> None:
        """Falls back to reading pyproject.toml when importlib.metadata fails."""
        from cagent.cli import _get_version

        with patch("importlib.metadata.version", side_effect=Exception("not installed")), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.read_text", return_value='version = "2.5.0"\n'):
            assert _get_version() == "2.5.0"

    def test_version_returns_unknown_on_failure(self) -> None:
        """Returns 'unknown' when all methods fail."""
        from cagent.cli import _get_version

        with patch("importlib.metadata.version", side_effect=Exception("fail")), \
             patch("pathlib.Path.exists", return_value=False):
            assert _get_version() == "unknown"

    def test_version_pyproject_no_match(self) -> None:
        """Returns 'unknown' when pyproject.toml exists but has no version."""
        from cagent.cli import _get_version

        with patch("importlib.metadata.version", side_effect=Exception("fail")), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.read_text", return_value="name = 'cagent'\n"):
            assert _get_version() == "unknown"


class TestMainRouting:
    """Tests for main() subcommand routing.

    All tests patch sys.platform='linux' to skip Windows stdout replacement
    in main() which breaks pytest capture.
    """

    _PLAT = patch("sys.platform", "linux")

    def test_main_no_command_prints_help(self) -> None:
        """No subcommand prints help and exits 0."""
        from cagent.cli import main
        with self._PLAT, patch("sys.argv", ["cagent"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_main_routes_run(self) -> None:
        """'run' subcommand calls _cmd_run."""
        from cagent.cli import main
        with self._PLAT, patch("sys.argv", ["cagent", "run", "tasks.txt"]), \
             patch("cagent.cli.run._cmd_run") as mock_run:
            main()
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert args.command == "run"
            assert args.tasks_file == "tasks.txt"

    def test_main_routes_status(self) -> None:
        """'status' subcommand calls _cmd_status."""
        from cagent.cli import main
        with self._PLAT, patch("sys.argv", ["cagent", "status"]), \
             patch("cagent.cli.watch._cmd_status") as mock_status:
            main()
            mock_status.assert_called_once()

    def test_main_routes_watch(self) -> None:
        """'watch' subcommand calls _cmd_watch."""
        from cagent.cli import main
        with self._PLAT, patch("sys.argv", ["cagent", "watch"]), \
             patch("cagent.cli.watch._cmd_watch") as mock_watch:
            main()
            mock_watch.assert_called_once()

    def test_main_routes_log(self) -> None:
        """'log' subcommand calls _cmd_log."""
        from cagent.cli import main
        with self._PLAT, patch("sys.argv", ["cagent", "log", "task-001"]), \
             patch("cagent.cli.logcmd._cmd_log") as mock_log:
            main()
            mock_log.assert_called_once()

    def test_main_routes_clean(self) -> None:
        """'clean' subcommand calls _cmd_clean."""
        from cagent.cli import main
        with self._PLAT, patch("sys.argv", ["cagent", "clean"]), \
             patch("cagent.cli.misc._cmd_clean") as mock_clean:
            main()
            mock_clean.assert_called_once()

    def test_main_routes_push(self) -> None:
        """'push' subcommand calls _cmd_push."""
        from cagent.cli import main
        with self._PLAT, patch("sys.argv", ["cagent", "push", "my-branch"]), \
             patch("cagent.cli.misc._cmd_push") as mock_push:
            main()
            mock_push.assert_called_once()
            args = mock_push.call_args[0][0]
            assert args.branch == "my-branch"

    def test_main_routes_branches(self) -> None:
        """'branches' subcommand calls _cmd_branches."""
        from cagent.cli import main
        with self._PLAT, patch("sys.argv", ["cagent", "branches"]), \
             patch("cagent.cli.misc._cmd_branches") as mock_branches:
            main()
            mock_branches.assert_called_once()

    def test_main_routes_plan(self) -> None:
        """'plan' subcommand calls _cmd_plan."""
        from cagent.cli import main
        with self._PLAT, patch("sys.argv", ["cagent", "plan", "add feature X"]), \
             patch("cagent.cli.plan._cmd_plan") as mock_plan:
            main()
            mock_plan.assert_called_once()
            args = mock_plan.call_args[0][0]
            assert args.goal == "add feature X"

    def test_main_routes_cancel(self) -> None:
        """'cancel' subcommand calls _cmd_cancel."""
        from cagent.cli import main
        with self._PLAT, patch("sys.argv", ["cagent", "cancel", "001"]), \
             patch("cagent.cli.misc._cmd_cancel") as mock_cancel:
            main()
            mock_cancel.assert_called_once()

    def test_main_run_with_options(self) -> None:
        """'run' subcommand parses all options correctly."""
        from cagent.cli import main
        with self._PLAT, patch("sys.argv", [
            "cagent", "run", "tasks.txt",
            "-j", "8",
            "--base", "main",
            "--squash",
            "--strategy", "merge",
            "--timeout", "600",
            "--retries", "3",
            "--quiet",
            "--dry-run",
            "--force",
            "--worker-model", "claude-haiku-4-5",
            "--integrator-model", "claude-opus-4-7",
            "--max-turns", "50",
            "--max-tokens", "100000",
        ]), \
             patch("cagent.cli.run._cmd_run") as mock_run:
            main()
            args = mock_run.call_args[0][0]
            assert args.jobs == 8
            assert args.base == "main"
            assert args.squash is True
            assert args.strategy == "merge"
            assert args.timeout == 600
            assert args.retries == 3
            assert args.quiet is True
            assert args.dry_run is True
            assert args.force is True
            assert args.worker_model == "claude-haiku-4-5"
            assert args.integrator_model == "claude-opus-4-7"
            assert args.max_turns == 50
            assert args.max_tokens == 100000

    def test_main_log_with_options(self) -> None:
        """'log' subcommand parses follow, raw, kind options."""
        from cagent.cli import main
        with self._PLAT, patch("sys.argv", [
            "cagent", "log", "task-001",
            "--run", "my-run",
            "-f",
            "--raw",
            "--kind", "error",
        ]), \
             patch("cagent.cli.logcmd._cmd_log") as mock_log:
            main()
            args = mock_log.call_args[0][0]
            assert args.task_id == "task-001"
            assert args.run == "my-run"
            assert args.follow is True
            assert args.raw is True
            assert args.kind == "error"

    def test_main_watch_with_web(self) -> None:
        """'watch --web 9090' parses web port option."""
        from cagent.cli import main
        with self._PLAT, patch("sys.argv", ["cagent", "watch", "--web", "9090"]), \
             patch("cagent.cli.watch._cmd_watch") as mock_watch:
            main()
            args = mock_watch.call_args[0][0]
            assert args.web == 9090
