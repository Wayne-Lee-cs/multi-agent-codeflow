"""Unit tests for cagent.cli.run — dispatch/integrate/summary phases + execute_run."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cagent.tasks import Task


@dataclass
class FakeResult:
    task_id: str
    status: str
    commit_sha: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    fail_reason: str | None = None
    output_summary: str = ""


class TestPrintTaskTiming:
    """Tests for _print_task_timing."""

    def test_prints_timing_for_tasks(self, capsys):
        from cagent.cli.run import _print_task_timing

        dashboard = MagicMock()
        dashboard.tasks = {
            "001": MagicMock(
                started_at=1000.0, ended_at=1060.0, status="done",
                commit_sha="a" * 40, tool_count=5,
            ),
            "002": MagicMock(
                started_at=1000.0, ended_at=None, status="running",
                commit_sha=None, tool_count=2,
            ),
        }

        _print_task_timing(dashboard)
        out = capsys.readouterr().out
        assert "Task timing:" in out
        assert "[001]" in out
        assert "[002]" in out
        assert "still running" in out

    def test_empty_dashboard(self, capsys):
        from cagent.cli.run import _print_task_timing

        dashboard = MagicMock()
        dashboard.tasks = {}

        _print_task_timing(dashboard)
        out = capsys.readouterr().out
        assert "Task timing" not in out

    def test_task_without_timing(self, capsys):
        from cagent.cli.run import _print_task_timing

        dashboard = MagicMock()
        dashboard.tasks = {
            "001": MagicMock(
                started_at=None, ended_at=None, status="pending",
                commit_sha=None, tool_count=0,
            ),
        }

        _print_task_timing(dashboard)
        out = capsys.readouterr().out
        assert "[001]" in out


class TestDispatchPhase:
    """Tests for _dispatch_phase."""

    @pytest.mark.asyncio
    async def test_dispatch_normal_path(self, tmp_path):
        from cagent.cli.run import _dispatch_phase

        dispatch_tasks = [
            Task(id="001", prompt="Task A", branch="task-001"),
            Task(id="002", prompt="Task B", branch="task-002"),
        ]
        all_tasks = list(dispatch_tasks)
        args = MagicMock()
        args.jobs = 2
        args.timeout = 300
        args.worker_model = None
        args.max_turns = None
        args.max_tokens = None
        args.retries = 0

        results = [
            FakeResult(task_id="001", status="done"),
            FakeResult(task_id="002", status="failed", fail_reason="timeout"),
        ]

        dashboard = MagicMock()
        memory = MagicMock()

        with patch("cagent.dispatcher.run", new_callable=AsyncMock, return_value=results):
            returned = await _dispatch_phase(
                dispatch_tasks, all_tasks, args, tmp_path, "b" * 40,
                tmp_path, dashboard, memory, "", None, None,
            )

        assert len(returned) == 2
        assert returned[0].status == "done"
        assert returned[1].status == "failed"

    @pytest.mark.asyncio
    async def test_dispatch_with_merge_results(self, tmp_path):
        from cagent.cli.run import _dispatch_phase

        dispatch_tasks = [Task(id="002", prompt="Task B", branch="task-002")]
        all_tasks = [
            Task(id="001", prompt="Task A", branch="task-001", status="done"),
            Task(id="002", prompt="Task B", branch="task-002"),
        ]
        args = MagicMock()
        args.jobs = 1
        args.timeout = 300
        args.worker_model = None
        args.max_turns = None
        args.max_tokens = None
        args.retries = 0

        dispatch_results = [FakeResult(task_id="002", status="done")]

        def merge_fn(all_t, dispatch_r):
            return [
                FakeResult(task_id="001", status="done"),
                FakeResult(task_id="002", status="done"),
            ]

        dashboard = MagicMock()
        memory = MagicMock()

        with patch("cagent.dispatcher.run", new_callable=AsyncMock, return_value=dispatch_results):
            returned = await _dispatch_phase(
                dispatch_tasks, all_tasks, args, tmp_path, "b" * 40,
                tmp_path, dashboard, memory, "", None, merge_fn,
            )

        assert len(returned) == 2

    @pytest.mark.asyncio
    async def test_dispatch_counts_statuses(self, tmp_path, capsys):
        from cagent.cli.run import _dispatch_phase

        dispatch_tasks = [
            Task(id="001", prompt="A", branch="t-001"),
            Task(id="002", prompt="B", branch="t-002"),
            Task(id="003", prompt="C", branch="t-003"),
        ]
        all_tasks = list(dispatch_tasks)
        args = MagicMock()
        args.jobs = 2
        args.timeout = 300
        args.worker_model = None
        args.max_turns = None
        args.max_tokens = None
        args.retries = 0

        results = [
            FakeResult(task_id="001", status="done"),
            FakeResult(task_id="002", status="failed"),
            FakeResult(task_id="003", status="noop"),
        ]

        dashboard = MagicMock()
        memory = MagicMock()

        with patch("cagent.dispatcher.run", new_callable=AsyncMock, return_value=results):
            await _dispatch_phase(
                dispatch_tasks, all_tasks, args, tmp_path, "b" * 40,
                tmp_path, dashboard, memory, "", None, None,
            )

        out = capsys.readouterr().out
        assert "1 done, 1 failed, 1 noop" in out


class TestIntegratePhase:
    """Tests for _integrate_phase."""

    @pytest.mark.asyncio
    async def test_integrate_normal_path(self, tmp_path):
        from cagent.cli.run import _integrate_phase

        all_tasks = [Task(id="001", prompt="Task", branch="task-001")]
        all_results = [FakeResult(task_id="001", status="done")]
        args = MagicMock()
        args.strategy = "cherry-pick"
        args.squash = False
        args.integrator_model = None
        args.timeout = 300
        args.post_integrate_cmd = None

        dashboard = MagicMock()
        memory = MagicMock()
        memory.read_all.return_value = {"001": "task output"}

        with patch("cagent.integrator.integrate", new_callable=AsyncMock, return_value="sha123"):
            result = await _integrate_phase(
                all_tasks, all_results, "run-001", tmp_path, "b" * 40,
                tmp_path, args, dashboard, memory, None,
            )

        assert result == "sha123"
        memory.write_shared.assert_called_once()

    @pytest.mark.asyncio
    async def test_integrate_no_done_tasks(self, tmp_path):
        from cagent.cli.run import _integrate_phase

        all_tasks = [Task(id="001", prompt="Task", branch="task-001")]
        all_results = [FakeResult(task_id="001", status="failed")]
        args = MagicMock()

        dashboard = MagicMock()
        memory = MagicMock()
        memory.read_all.return_value = {}

        result = await _integrate_phase(
            all_tasks, all_results, "run-001", tmp_path, "b" * 40,
            tmp_path, args, dashboard, memory, None,
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_integrate_exception_returns_none(self, tmp_path, capsys):
        from cagent.cli.run import _integrate_phase

        all_tasks = [Task(id="001", prompt="Task", branch="task-001")]
        all_results = [FakeResult(task_id="001", status="done")]
        args = MagicMock()
        args.strategy = "cherry-pick"
        args.squash = False
        args.integrator_model = None
        args.timeout = 300
        args.post_integrate_cmd = None

        dashboard = MagicMock()
        memory = MagicMock()
        memory.read_all.return_value = {}

        with patch("cagent.integrator.integrate", new_callable=AsyncMock, side_effect=RuntimeError("git failed")):
            result = await _integrate_phase(
                all_tasks, all_results, "run-001", tmp_path, "b" * 40,
                tmp_path, args, dashboard, memory, None,
            )

        assert result is None
        err = capsys.readouterr().out
        assert "FAILED" in err


class TestSummaryPhase:
    """Tests for _summary_phase."""

    def test_summary_with_integration(self, tmp_path, capsys):
        from cagent.cli.run import _summary_phase

        tasks = [Task(id="001", prompt="Task", branch="task-001", status="done")]
        results = [FakeResult(task_id="001", status="done")]
        args = MagicMock()
        args.keep_worktrees = True

        with patch("cagent.cli.run._write_summary") as mock_write:
            _summary_phase(
                tasks, results, "run-001", tmp_path, "b" * 40,
                tmp_path, "c" * 40, "2m30s", args,
            )

        mock_write.assert_called_once()
        out = capsys.readouterr().out
        assert "Done!" in out
        assert "run-001" in out

    def test_summary_no_integration(self, tmp_path, capsys):
        from cagent.cli.run import _summary_phase

        tasks = [Task(id="001", prompt="Task", branch="task-001", status="failed")]
        results = [FakeResult(task_id="001", status="failed")]
        args = MagicMock()
        args.keep_worktrees = True

        with patch("cagent.cli.run._write_summary"):
            _summary_phase(
                tasks, results, "run-001", tmp_path, "b" * 40,
                tmp_path, None, "1m0s", args,
            )

        out = capsys.readouterr().out
        assert "no successful tasks" in out

    def test_summary_cleans_worktrees(self, tmp_path):
        from cagent.cli.run import _summary_phase

        tasks = [Task(id="001", prompt="Task", branch="task-001", status="done")]
        results = [FakeResult(task_id="001", status="done")]
        args = MagicMock()
        args.keep_worktrees = False

        with patch("cagent.cli.run._write_summary"), \
             patch("cagent.cli.run._clean_worktrees") as mock_clean:
            _summary_phase(
                tasks, results, "run-001", tmp_path, "b" * 40,
                tmp_path, "c" * 40, "1m0s", args,
            )

        mock_clean.assert_called_once()

    def test_summary_shows_memory_dir(self, tmp_path, capsys):
        from cagent.cli.run import _summary_phase

        run_dir = tmp_path / "run"
        run_dir.mkdir()
        mem_dir = run_dir / "memory"
        mem_dir.mkdir()
        (mem_dir / "task-001.md").write_text("memory", encoding="utf-8")

        tasks = [Task(id="001", prompt="Task", branch="task-001", status="done")]
        results = [FakeResult(task_id="001", status="done")]
        args = MagicMock()
        args.keep_worktrees = True

        with patch("cagent.cli.run._write_summary"), \
             patch("cagent.cli.run._prompt_clean_memory"):
            _summary_phase(
                tasks, results, "run-001", run_dir, "b" * 40,
                tmp_path, "c" * 40, "1m0s", args,
            )

        out = capsys.readouterr().out
        assert "memory" in out.lower()


class TestExecuteRun:
    """Tests for _execute_run — the main orchestrator."""

    def test_execute_run_normal_path(self, tmp_path):
        from cagent.cli.run import _execute_run

        tasks = [
            Task(id="001", prompt="Task A", branch="task-001"),
            Task(id="002", prompt="Task B", branch="task-002"),
        ]

        args = MagicMock()
        args.quiet = True
        args.keep_worktrees = True

        results = [
            FakeResult(task_id="001", status="done"),
            FakeResult(task_id="002", status="done"),
        ]

        async def mock_flush():
            pass

        async def mock_stop():
            pass

        def _mock_asyncio_run(coro):
            coro.close()
            return (results, "sha" * 10)

        # asyncio.run returns (results, integration_sha) directly,
        # so _dispatch_phase/_integrate_phase patches are unnecessary.
        with patch("cagent.cli.run._summary_phase") as mock_summary, \
             patch("cagent.progress.Dashboard") as MockDashboard, \
             patch("cagent.memory.RunMemory"), \
             patch("cagent.log.LinePrinter"), \
             patch("cagent.cli.run.asyncio.run", side_effect=_mock_asyncio_run), \
             patch("cagent.cli.run.time.time", side_effect=[0, 60]):
            MockDashboard.return_value.flush_async = mock_flush
            MockDashboard.return_value.stop_async_io = mock_stop
            MockDashboard.return_value.set_event_handler = MagicMock()
            MockDashboard.return_value.start_async_io = MagicMock()

            _execute_run(
                all_tasks=tasks,
                dispatch_tasks=tasks,
                run_id="run-001",
                run_dir=tmp_path,
                base_sha="b" * 40,
                repo_root=tmp_path,
                args=args,
            )

        mock_summary.assert_called_once()

    def test_execute_run_keyboard_interrupt(self, tmp_path, capsys):
        from cagent.cli.run import _execute_run

        tasks = [Task(id="001", prompt="Task", branch="task-001")]

        args = MagicMock()
        args.quiet = True

        pids_dir = tmp_path / "pids"
        pids_dir.mkdir()
        (pids_dir / "task-001.pid").write_text("12345", encoding="utf-8")

        def _raise_ki(coro):
            coro.close()
            raise KeyboardInterrupt

        with patch("cagent.progress.Dashboard"), \
             patch("cagent.memory.RunMemory"), \
             patch("cagent.log.LinePrinter"), \
             patch("cagent.cli.run.asyncio.run", side_effect=_raise_ki), \
             patch("cagent.cli.run.time.time", side_effect=[0, 60]), \
             patch("cagent.cli.base._terminate_pid") as mock_term, \
             patch("cagent.tasks.dump_state"), \
             pytest.raises(SystemExit, match="130"):
            _execute_run(
                all_tasks=tasks,
                dispatch_tasks=tasks,
                run_id="run-001",
                run_dir=tmp_path,
                base_sha="b" * 40,
                repo_root=tmp_path,
                args=args,
            )

        mock_term.assert_called_once_with(12345)
        out = capsys.readouterr().out
        assert "Interrupted" in out

    def test_execute_run_unhandled_exception(self, tmp_path, capsys):
        from cagent.cli.run import _execute_run

        tasks = [Task(id="001", prompt="Task", branch="task-001")]
        args = MagicMock()
        args.quiet = True

        def _raise_re(coro):
            coro.close()
            raise RuntimeError("unexpected")

        with patch("cagent.progress.Dashboard"), \
             patch("cagent.memory.RunMemory"), \
             patch("cagent.log.LinePrinter"), \
             patch("cagent.cli.run.asyncio.run", side_effect=_raise_re), \
             patch("cagent.cli.run.time.time", side_effect=[0, 60]), \
             patch("cagent.tasks.dump_state"), \
             pytest.raises(SystemExit, match="1"):
            _execute_run(
                all_tasks=tasks,
                dispatch_tasks=tasks,
                run_id="run-001",
                run_dir=tmp_path,
                base_sha="b" * 40,
                repo_root=tmp_path,
                args=args,
            )

        err = capsys.readouterr().err
        assert "unexpected" in err


class TestWriteSummaryDashboardFallback:
    """Tests for _write_summary dashboard token fallback."""

    def test_dashboard_tokens_override_results(self, tmp_path):
        from cagent.cli.run import _write_summary

        run_dir = tmp_path / "run"
        run_dir.mkdir()

        # Dashboard has higher token counts than results
        dashboard_data = {
            "001": {"tokens_in": 5000, "tokens_out": 2000},
            "002": {"tokens_in": 3000, "tokens_out": 1000},
        }
        (run_dir / "dashboard.json").write_text(
            json.dumps(dashboard_data), encoding="utf-8"
        )

        tasks = [
            Task(id="001", prompt="Task A", branch="task-001", status="done"),
            Task(id="002", prompt="Task B", branch="task-002", status="done"),
        ]
        # Results have lower token counts
        results = [
            FakeResult(task_id="001", status="done", tokens_in=100, tokens_out=50),
            FakeResult(task_id="002", status="done", tokens_in=100, tokens_out=50),
        ]

        _write_summary(
            run_dir=run_dir,
            tasks=tasks,
            results=results,
            base_sha="b" * 40,
            integration_sha="c" * 40,
            run_id="test-run",
        )

        summary = (run_dir / "summary.md").read_text(encoding="utf-8")
        # Should use dashboard totals (8000 in, 3000 out) not result totals (200 in, 100 out)
        assert "8,000" in summary
        assert "3,000" in summary

    def test_budget_percentage_in_summary(self, tmp_path):
        from cagent.cli.run import _write_summary

        run_dir = tmp_path / "run"
        run_dir.mkdir()

        (run_dir / "budget.json").write_text(
            json.dumps({"max_tokens": 100000}), encoding="utf-8"
        )

        tasks = [
            Task(id="001", prompt="Task", branch="task-001", status="done"),
        ]
        results = [
            FakeResult(task_id="001", status="done", tokens_in=40000, tokens_out=10000),
        ]

        _write_summary(
            run_dir=run_dir,
            tasks=tasks,
            results=results,
            base_sha="b" * 40,
            integration_sha="c" * 40,
            run_id="test-run",
        )

        summary = (run_dir / "summary.md").read_text(encoding="utf-8")
        assert "100,000" in summary
        assert "50%" in summary


class TestCmdRunInner:
    """Tests for _cmd_run_inner — full run path."""

    def test_run_inner_with_tasks_md(self, tmp_path):
        from cagent.cli.run import _cmd_run_inner

        tasks_file = tmp_path / "tasks.md"
        tasks_file.write_text("# Tasks\n\n## Task 001\nDo something\n", encoding="utf-8")

        args = MagicMock()
        args.dry_run = False
        args.base = None
        args.tasks_file = str(tasks_file)
        args.jobs = 1
        args.timeout = 300
        args.squash = False
        args.strategy = "cherry-pick"
        args.worker_model = None
        args.max_turns = None
        args.max_tokens = None
        args.api_key = None
        args.api_key_file = None

        with patch("cagent.worktree.current_head", return_value="a" * 40), \
             patch("cagent.tasks.parse_tasks_md", return_value=(
                 [Task(id="001", prompt="Do something", branch="task-001")],
                 "conventions here",
             )), \
             patch("cagent.cli.run._execute_run") as mock_exec:
            _cmd_run_inner(args, tmp_path)

        mock_exec.assert_called_once()
        # Verify conventions.txt was written
        runs_dirs = list((tmp_path / ".cagent" / "runs").iterdir())
        assert len(runs_dirs) == 1
        assert (runs_dirs[0] / "conventions.txt").exists()

    def test_run_inner_with_base_branch(self, tmp_path):
        from cagent.cli.run import _cmd_run_inner

        tasks_file = tmp_path / "tasks.txt"
        tasks_file.write_text("Task one\n", encoding="utf-8")

        args = MagicMock()
        args.dry_run = False
        args.base = "main"
        args.tasks_file = str(tasks_file)
        args.jobs = 1
        args.timeout = 300
        args.squash = False
        args.strategy = "cherry-pick"
        args.worker_model = None
        args.max_turns = None
        args.max_tokens = None
        args.api_key = None
        args.api_key_file = None

        mock_result = MagicMock()
        mock_result.stdout = "abc123\n"

        with patch("cagent.cli.run.run_git", return_value=mock_result), \
             patch("cagent.tasks.parse_tasks_file", return_value=[
                 Task(id="001", prompt="Task one", branch="task-001"),
             ]), \
             patch("cagent.cli.run._execute_run") as mock_exec:
            _cmd_run_inner(args, tmp_path)

        mock_exec.assert_called_once()
        call_kwargs = mock_exec.call_args[1]
        assert call_kwargs["base_sha"] == "abc123"

    def test_run_inner_invalid_base_exits(self, tmp_path, capsys):
        from cagent.cli.run import _cmd_run_inner

        tasks_file = tmp_path / "tasks.txt"
        tasks_file.write_text("Task\n", encoding="utf-8")

        args = MagicMock()
        args.dry_run = False
        args.base = "nonexistent"
        args.tasks_file = str(tasks_file)

        with patch("cagent.cli.run.run_git", side_effect=RuntimeError("git failed")), \
             pytest.raises(SystemExit, match="1"):
            _cmd_run_inner(args, tmp_path)

        err = capsys.readouterr().err
        assert "invalid base" in err

    def test_run_inner_tasks_file_not_found(self, tmp_path, capsys):
        from cagent.cli.run import _cmd_run_inner

        args = MagicMock()
        args.dry_run = False
        args.base = None
        args.tasks_file = str(tmp_path / "nonexistent.txt")

        with patch("cagent.worktree.current_head", return_value="a" * 40), \
             pytest.raises(SystemExit, match="1"):
            _cmd_run_inner(args, tmp_path)

        err = capsys.readouterr().err
        assert "Error" in err

    def test_run_inner_writes_budget_json(self, tmp_path):
        from cagent.cli.run import _cmd_run_inner

        tasks_file = tmp_path / "tasks.txt"
        tasks_file.write_text("Task\n", encoding="utf-8")

        args = MagicMock()
        args.dry_run = False
        args.base = None
        args.tasks_file = str(tasks_file)
        args.jobs = 1
        args.timeout = 300
        args.squash = False
        args.strategy = "cherry-pick"
        args.worker_model = None
        args.max_turns = None
        args.max_tokens = 50000
        args.api_key = None
        args.api_key_file = None

        with patch("cagent.worktree.current_head", return_value="a" * 40), \
             patch("cagent.tasks.parse_tasks_file", return_value=[
                 Task(id="001", prompt="Task", branch="task-001"),
             ]), \
             patch("cagent.cli.run._execute_run"):
            _cmd_run_inner(args, tmp_path)

        runs_dirs = list((tmp_path / ".cagent" / "runs").iterdir())
        budget_path = runs_dirs[0] / "budget.json"
        assert budget_path.exists()
        data = json.loads(budget_path.read_text(encoding="utf-8"))
        assert data["max_tokens"] == 50000


class TestCmdRun:
    """Tests for _cmd_run — the entry point."""

    def test_cmd_run_calls_resume(self, tmp_path):
        from cagent.cli.run import _cmd_run

        args = MagicMock()
        args.resume = "some-run"
        args.force = False
        args.api_key = None
        args.api_key_file = None

        with patch("cagent.cli.run._get_repo_root", return_value=tmp_path), \
             patch("cagent.config.apply_config"), \
             patch("cagent.config.load_config", return_value={}), \
             patch("cagent.cli.run._preflight_check"), \
             patch("cagent.cli.run._run_lock", return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock())), \
             patch("cagent.cli.run._cmd_resume") as mock_resume:
            _cmd_run(args)

        mock_resume.assert_called_once()

    def test_cmd_run_calls_run_inner(self, tmp_path):
        from cagent.cli.run import _cmd_run

        args = MagicMock()
        args.resume = None
        args.force = False
        args.api_key = None
        args.api_key_file = None

        with patch("cagent.cli.run._get_repo_root", return_value=tmp_path), \
             patch("cagent.config.apply_config"), \
             patch("cagent.config.load_config", return_value={}), \
             patch("cagent.cli.run._preflight_check"), \
             patch("cagent.cli.run._run_lock", return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock())), \
             patch("cagent.cli.run._cmd_run_inner") as mock_inner:
            _cmd_run(args)

        mock_inner.assert_called_once()


class TestResumeEdgeCases:
    """Additional edge cases for _cmd_resume."""

    def test_resume_with_conventions(self, tmp_path):
        from cagent.cli.run import _cmd_resume

        run_dir = tmp_path / ".cagent" / "runs" / "test-run"
        run_dir.mkdir(parents=True)
        (run_dir / "base_sha").write_text("b" * 40, encoding="utf-8")
        (run_dir / "conventions.txt").write_text("Use TypeScript", encoding="utf-8")

        tasks_data = [
            {"id": "001", "prompt": "Task", "branch": "task-001", "status": "failed"},
        ]
        (run_dir / "tasks.json").write_text(json.dumps(tasks_data), encoding="utf-8")

        args = MagicMock()
        args.resume = "test-run"
        args.api_key = None
        args.api_key_file = None

        with patch("cagent.cli.run._execute_run") as mock_exec, \
             patch("cagent.cli.run.run_git"):
            _cmd_resume(args, tmp_path)

        call_kwargs = mock_exec.call_args[1]
        assert call_kwargs["conventions"] == "Use TypeScript"

    def test_resume_base_sha_fallback(self, tmp_path, capsys):
        from cagent.cli.run import _cmd_resume

        run_dir = tmp_path / ".cagent" / "runs" / "test-run"
        run_dir.mkdir(parents=True)
        # No base_sha file

        tasks_data = [
            {"id": "001", "prompt": "Task", "branch": "task-001", "status": "failed"},
        ]
        (run_dir / "tasks.json").write_text(json.dumps(tasks_data), encoding="utf-8")

        args = MagicMock()
        args.resume = "test-run"
        args.api_key = None
        args.api_key_file = None

        with patch("cagent.cli.run._execute_run") as mock_exec, \
             patch("cagent.cli.run.run_git"), \
             patch("cagent.worktree.current_head", return_value="head123"):
            _cmd_resume(args, tmp_path)

        call_kwargs = mock_exec.call_args[1]
        assert call_kwargs["base_sha"] == "head123"
        warn = capsys.readouterr().err
        assert "Warning" in warn

    def test_resume_skips_active_pid_tasks(self, tmp_path):
        from cagent.cli.run import _cmd_resume

        run_dir = tmp_path / ".cagent" / "runs" / "test-run"
        run_dir.mkdir(parents=True)
        (run_dir / "base_sha").write_text("b" * 40, encoding="utf-8")

        pid_dir = run_dir / "pids"
        pid_dir.mkdir()
        (pid_dir / "task-001.pid").write_text("99999", encoding="utf-8")

        tasks_data = [
            {"id": "001", "prompt": "Task", "branch": "task-001", "status": "running"},
        ]
        (run_dir / "tasks.json").write_text(json.dumps(tasks_data), encoding="utf-8")

        args = MagicMock()
        args.resume = "test-run"
        args.api_key = None
        args.api_key_file = None

        with patch("cagent.cli.run._execute_run") as mock_exec, \
             patch("cagent.cli.run.run_git"), \
             patch("cagent.cli.run._is_pid_active", return_value=True):
            _cmd_resume(args, tmp_path)

        # Task should be filtered out (still running), so dispatch_tasks is empty
        call_kwargs = mock_exec.call_args[1]
        assert len(call_kwargs["dispatch_tasks"]) == 0


class TestResolveApiKey:
    """Tests for _resolve_api_key — API key file support."""

    def test_no_key_returns_none(self):
        from cagent.cli.run import _resolve_api_key
        args = MagicMock()
        args.api_key_file = None
        args.api_key = None
        args.api_key_file = None
        assert _resolve_api_key(args) is None

    def test_api_key_direct(self):
        from cagent.cli.run import _resolve_api_key
        args = MagicMock()
        args.api_key_file = None
        args.api_key = "sk-ant-direct"
        assert _resolve_api_key(args) == "sk-ant-direct"

    def test_api_key_file(self, tmp_path):
        from cagent.cli.run import _resolve_api_key
        key_file = tmp_path / "key.txt"
        key_file.write_text("sk-ant-from-file\n", encoding="utf-8")
        args = MagicMock()
        args.api_key_file = str(key_file)
        args.api_key = "sk-ant-ignored"
        assert _resolve_api_key(args) == "sk-ant-from-file"

    def test_api_key_file_not_found(self, tmp_path):
        from cagent.cli.run import _resolve_api_key
        args = MagicMock()
        args.api_key_file = str(tmp_path / "nonexistent.txt")
        args.api_key = None
        with pytest.raises(SystemExit):
            _resolve_api_key(args)

    def test_api_key_file_empty(self, tmp_path):
        from cagent.cli.run import _resolve_api_key
        key_file = tmp_path / "empty.txt"
        key_file.write_text("  \n", encoding="utf-8")
        args = MagicMock()
        args.api_key_file = str(key_file)
        args.api_key = None
        with pytest.raises(SystemExit):
            _resolve_api_key(args)

    def test_api_key_file_takes_precedence(self, tmp_path):
        from cagent.cli.run import _resolve_api_key
        key_file = tmp_path / "key.txt"
        key_file.write_text("sk-from-file", encoding="utf-8")
        args = MagicMock()
        args.api_key_file = str(key_file)
        args.api_key = "sk-from-arg"
        assert _resolve_api_key(args) == "sk-from-file"
