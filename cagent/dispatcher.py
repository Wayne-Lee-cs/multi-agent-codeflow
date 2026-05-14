"""Async dispatcher — run tasks concurrently with bounded parallelism."""

from __future__ import annotations

import asyncio
from pathlib import Path

from cagent.agent import AgentResult, run_agent
from cagent.progress import Dashboard
from cagent.tasks import Task, dump_state
from cagent.worktree import create_worktree


async def run(
    tasks: list[Task],
    concurrency: int,
    run_dir: Path,
    base_sha: str,
    repo_root: Path,
    worker_model_override: str | None = None,
    timeout: int = 1800,
    dashboard: Dashboard | None = None,
) -> list[AgentResult]:
    """Run all tasks concurrently with bounded parallelism.

    Each task gets its own git worktree. Results are collected and returned
    in the same order as the input tasks list.
    """
    sem = asyncio.Semaphore(concurrency)
    results: dict[str, AgentResult] = {}
    lock = asyncio.Lock()

    async def _run_one(task: Task) -> None:
        async with sem:
            try:
                # Create worktree
                worktree_path = repo_root / ".cagent" / "worktrees" / run_dir.name / f"task-{task.id}"
                try:
                    create_worktree(repo_root, worktree_path, task.branch, base_sha)
                except Exception as e:
                    result = AgentResult(
                        task_id=task.id,
                        status="failed",
                        fail_reason=f"worktree creation failed: {e}",
                    )
                    async with lock:
                        results[task.id] = result
                        task.status = "failed"
                        if dashboard:
                            dashboard.set_task_status(task.id, "failed", fail_reason=str(e))
                        dump_state(run_dir, tasks)
                    return

                # Update task status
                async with lock:
                    task.status = "running"
                    dump_state(run_dir, tasks)

                # Run agent
                result = await run_agent(
                    task=task,
                    worktree_path=worktree_path,
                    run_dir=run_dir,
                    timeout=timeout,
                    model_override=worker_model_override,
                    dashboard=dashboard,
                )

                # Update task with result
                async with lock:
                    task.status = result.status
                    task.commit_sha = result.commit_sha
                    results[task.id] = result
                    dump_state(run_dir, tasks)

            except Exception as e:
                result = AgentResult(
                    task_id=task.id,
                    status="failed",
                    fail_reason=f"unhandled error: {e}",
                )
                async with lock:
                    results[task.id] = result
                    task.status = "failed"
                    if dashboard:
                        dashboard.set_task_status(task.id, "failed", fail_reason=str(e))
                    dump_state(run_dir, tasks)

    # Use TaskGroup for structured concurrency
    async with asyncio.TaskGroup() as tg:
        for task in tasks:
            tg.create_task(_run_one(task))

    # Return results in the same order as input tasks
    return [
        results.get(t.id, AgentResult(task_id=t.id, status="failed", fail_reason="task was cancelled"))
        for t in tasks
    ]
