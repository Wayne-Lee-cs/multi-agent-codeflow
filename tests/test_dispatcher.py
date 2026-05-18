"""Unit tests for cagent/dispatcher.py — dependency graph scheduling."""

from __future__ import annotations

import asyncio
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

from cagent.agent import AgentResult
from cagent.dispatcher import run
from cagent.tasks import Task


def _make_task(task_id: str, prompt: str = "do something", depends_on: list[str] | None = None) -> Task:
    return Task(
        id=task_id,
        prompt=prompt,
        branch=f"cagent/test/task-{task_id}",
        status="pending",
        commit_sha=None,
        log_path=Path(f"/tmp/log-{task_id}.log"),
        depends_on=depends_on or [],
    )


def _mock_run_agent_success(task, worktree_path, run_dir, timeout=1800, **kwargs):
    """Mock run_agent that always succeeds."""
    return AgentResult(task_id=task.id, status="done", commit_sha=f"sha-{task.id}")


def _mock_run_agent_fail(task, worktree_path, run_dir, timeout=1800, **kwargs):
    """Mock run_agent that always fails."""
    return AgentResult(task_id=task.id, status="failed", fail_reason="mock failure")


@pytest.fixture
def run_dir(tmp_path):
    d = tmp_path / "runs" / "test"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def repo_root(tmp_path):
    return tmp_path


class TestNoDependencies:
    """Tasks without dependencies run concurrently (original behavior)."""

    @pytest.mark.asyncio
    async def test_all_tasks_run(self, run_dir, repo_root):
        tasks = [_make_task("001"), _make_task("002"), _make_task("003")]
        with patch("cagent.dispatcher.run_agent", side_effect=_mock_run_agent_success):
            with patch("cagent.dispatcher.create_worktree"):
                results = await run(tasks, concurrency=4, run_dir=run_dir, base_sha="abc123", repo_root=repo_root)
        assert all(r.status == "done" for r in results)
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_partial_failure(self, run_dir, repo_root):
        def mixed_runner(task, **kwargs):
            if task.id == "002":
                return AgentResult(task_id="002", status="failed", fail_reason="mock")
            return AgentResult(task_id=task.id, status="done", commit_sha=f"sha-{task.id}")

        tasks = [_make_task("001"), _make_task("002"), _make_task("003")]
        with patch("cagent.dispatcher.run_agent", side_effect=mixed_runner):
            with patch("cagent.dispatcher.create_worktree"):
                results = await run(tasks, concurrency=4, run_dir=run_dir, base_sha="abc123", repo_root=repo_root)
        statuses = {r.task_id: r.status for r in results}
        assert statuses["001"] == "done"
        assert statuses["002"] == "failed"
        assert statuses["003"] == "done"


class TestDependencyGraph:
    """Tasks with dependencies are executed in wave order."""

    @pytest.mark.asyncio
    async def test_linear_chain(self, run_dir, repo_root):
        """A → B → C should execute in order: A first, then B, then C."""
        execution_order = []

        async def tracking_runner(task, **kwargs):
            execution_order.append(task.id)
            # Small delay to ensure ordering is visible
            await asyncio.sleep(0.05)
            return AgentResult(task_id=task.id, status="done", commit_sha=f"sha-{task.id}")

        tasks = [
            _make_task("A"),
            _make_task("B", depends_on=["A"]),
            _make_task("C", depends_on=["B"]),
        ]
        with patch("cagent.dispatcher.run_agent", side_effect=tracking_runner):
            with patch("cagent.dispatcher.create_worktree"):
                results = await run(tasks, concurrency=4, run_dir=run_dir, base_sha="abc123", repo_root=repo_root)

        assert all(r.status == "done" for r in results)
        # A must come before B, B must come before C
        assert execution_order.index("A") < execution_order.index("B")
        assert execution_order.index("B") < execution_order.index("C")

    @pytest.mark.asyncio
    async def test_parallel_independent_tasks(self, run_dir, repo_root):
        """A, B (no deps) → C (depends on A,B): A and B run in parallel."""
        started = {}
        barrier = asyncio.Event()

        async def barrier_runner(task, **kwargs):
            started[task.id] = asyncio.get_event_loop().time()
            if task.id in ("A", "B"):
                # Both A and B should start before either finishes
                if "A" in started and "B" in started:
                    barrier.set()
                else:
                    try:
                        await asyncio.wait_for(barrier.wait(), timeout=1.0)
                    except TimeoutError:
                        pass
            return AgentResult(task_id=task.id, status="done", commit_sha=f"sha-{task.id}")

        tasks = [
            _make_task("A"),
            _make_task("B"),
            _make_task("C", depends_on=["A", "B"]),
        ]
        with patch("cagent.dispatcher.run_agent", side_effect=barrier_runner):
            with patch("cagent.dispatcher.create_worktree"):
                results = await run(tasks, concurrency=4, run_dir=run_dir, base_sha="abc123", repo_root=repo_root)

        assert all(r.status == "done" for r in results)
        # A and B should both be started (they ran in parallel)
        assert "A" in started
        assert "B" in started

    @pytest.mark.asyncio
    async def test_failed_dep_blocks_downstream(self, run_dir, repo_root):
        """A(fail) → B should mark B as blocked (not execute B)."""
        executed = []

        def mixed_runner(task, **kwargs):
            executed.append(task.id)
            if task.id == "A":
                return AgentResult(task_id="A", status="failed", fail_reason="mock")
            return AgentResult(task_id=task.id, status="done", commit_sha=f"sha-{task.id}")

        tasks = [
            _make_task("A"),
            _make_task("B", depends_on=["A"]),
        ]
        with patch("cagent.dispatcher.run_agent", side_effect=mixed_runner):
            with patch("cagent.dispatcher.create_worktree"):
                results = await run(tasks, concurrency=4, run_dir=run_dir, base_sha="abc123", repo_root=repo_root)

        statuses = {r.task_id: r.status for r in results}
        assert statuses["A"] == "failed"
        assert statuses["B"] == "failed"
        # B should NOT have been executed
        assert "B" not in executed
        # B's fail reason should mention blocked dependency
        b_result = next(r for r in results if r.task_id == "B")
        assert "blocked by failed dependency" in b_result.fail_reason

    @pytest.mark.asyncio
    async def test_partial_failure_blocks_transitive(self, run_dir, repo_root):
        """A(ok) → B(fail) → C: C should be blocked by B, not by A."""
        executed = []

        def mixed_runner(task, **kwargs):
            executed.append(task.id)
            if task.id == "B":
                return AgentResult(task_id="B", status="failed", fail_reason="mock")
            return AgentResult(task_id=task.id, status="done", commit_sha=f"sha-{task.id}")

        tasks = [
            _make_task("A"),
            _make_task("B", depends_on=["A"]),
            _make_task("C", depends_on=["B"]),
        ]
        with patch("cagent.dispatcher.run_agent", side_effect=mixed_runner):
            with patch("cagent.dispatcher.create_worktree"):
                results = await run(tasks, concurrency=4, run_dir=run_dir, base_sha="abc123", repo_root=repo_root)

        statuses = {r.task_id: r.status for r in results}
        assert statuses["A"] == "done"
        assert statuses["B"] == "failed"
        assert statuses["C"] == "failed"
        assert "C" not in executed

    @pytest.mark.asyncio
    async def test_diamond_dependency(self, run_dir, repo_root):
        """Diamond: A → B, A → C, B+C → D."""
        execution_order = []

        async def tracking_runner(task, **kwargs):
            execution_order.append(task.id)
            await asyncio.sleep(0.05)
            return AgentResult(task_id=task.id, status="done", commit_sha=f"sha-{task.id}")

        tasks = [
            _make_task("A"),
            _make_task("B", depends_on=["A"]),
            _make_task("C", depends_on=["A"]),
            _make_task("D", depends_on=["B", "C"]),
        ]
        with patch("cagent.dispatcher.run_agent", side_effect=tracking_runner):
            with patch("cagent.dispatcher.create_worktree"):
                results = await run(tasks, concurrency=4, run_dir=run_dir, base_sha="abc123", repo_root=repo_root)

        assert all(r.status == "done" for r in results)
        # A first
        assert execution_order[0] == "A"
        # B and C after A, before D
        assert execution_order.index("B") > execution_order.index("A")
        assert execution_order.index("C") > execution_order.index("A")
        assert execution_order.index("D") > execution_order.index("B")
        assert execution_order.index("D") > execution_order.index("C")


class TestCycleDetection:
    def test_cycle_raises(self, run_dir, repo_root):
        tasks = [
            _make_task("A", depends_on=["B"]),
            _make_task("B", depends_on=["A"]),
        ]
        with pytest.raises(ValueError, match="Circular dependency"):
            asyncio.run(run(tasks, concurrency=4, run_dir=run_dir, base_sha="abc123", repo_root=repo_root))

    def test_self_cycle_raises(self, run_dir, repo_root):
        tasks = [_make_task("A", depends_on=["A"])]
        with pytest.raises(ValueError, match="Circular dependency"):
            asyncio.run(run(tasks, concurrency=4, run_dir=run_dir, base_sha="abc123", repo_root=repo_root))


class TestInvalidDependencies:
    def test_nonexistent_dep_raises(self, run_dir, repo_root):
        tasks = [_make_task("A", depends_on=["Z"])]
        with pytest.raises(ValueError, match="non-existent task"):
            asyncio.run(run(tasks, concurrency=4, run_dir=run_dir, base_sha="abc123", repo_root=repo_root))


class TestRetry:
    """Retry logic for transient failures."""

    @pytest.mark.asyncio
    async def test_retry_timeout_then_success(self, run_dir, repo_root):
        """Timeout failure → retry → success."""
        call_count = {"count": 0}

        def runner(task, **kwargs):
            call_count["count"] += 1
            if call_count["count"] == 1:
                return AgentResult(task_id=task.id, status="failed", fail_reason="timeout")
            return AgentResult(task_id=task.id, status="done", commit_sha=f"sha-{task.id}")

        tasks = [_make_task("001")]
        with patch("cagent.dispatcher.run_agent", side_effect=runner):
            with patch("cagent.dispatcher.create_worktree"):
                results = await run(
                    tasks, concurrency=4, run_dir=run_dir,
                    base_sha="abc123", repo_root=repo_root, retries=1,
                )
        assert results[0].status == "done"
        assert call_count["count"] == 2

    @pytest.mark.asyncio
    async def test_retry_exhausted(self, run_dir, repo_root):
        """All retries exhausted → final failure."""
        call_count = {"count": 0}

        def runner(task, **kwargs):
            call_count["count"] += 1
            return AgentResult(task_id=task.id, status="failed", fail_reason="timeout")

        tasks = [_make_task("001")]
        with patch("cagent.dispatcher.run_agent", side_effect=runner):
            with patch("cagent.dispatcher.create_worktree"):
                results = await run(
                    tasks, concurrency=4, run_dir=run_dir,
                    base_sha="abc123", repo_root=repo_root, retries=2,
                )
        assert results[0].status == "failed"
        assert call_count["count"] == 3  # initial + 2 retries

    @pytest.mark.asyncio
    async def test_non_retryable_no_retry(self, run_dir, repo_root):
        """Non-retryable error (code error) → no retry."""
        call_count = {"count": 0}

        def runner(task, **kwargs):
            call_count["count"] += 1
            return AgentResult(task_id=task.id, status="failed", fail_reason="exit code 1")

        tasks = [_make_task("001")]
        with patch("cagent.dispatcher.run_agent", side_effect=runner):
            with patch("cagent.dispatcher.create_worktree"):
                results = await run(
                    tasks, concurrency=4, run_dir=run_dir,
                    base_sha="abc123", repo_root=repo_root, retries=3,
                )
        assert results[0].status == "failed"
        assert call_count["count"] == 1  # no retry

    @pytest.mark.asyncio
    async def test_retry_count_tracked(self, run_dir, repo_root):
        """task.retry_count is updated on each attempt."""
        call_count = {"count": 0}

        def runner(task, **kwargs):
            call_count["count"] += 1
            if call_count["count"] <= 2:
                return AgentResult(task_id=task.id, status="failed", fail_reason="timeout")
            return AgentResult(task_id=task.id, status="done", commit_sha=f"sha-{task.id}")

        tasks = [_make_task("001")]
        with patch("cagent.dispatcher.run_agent", side_effect=runner):
            with patch("cagent.dispatcher.create_worktree"):
                results = await run(
                    tasks, concurrency=4, run_dir=run_dir,
                    base_sha="abc123", repo_root=repo_root, retries=2,
                )
        assert results[0].status == "done"
        assert tasks[0].retry_count == 2  # last attempt index
