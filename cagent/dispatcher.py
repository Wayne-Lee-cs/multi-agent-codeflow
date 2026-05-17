"""Async dispatcher — run tasks concurrently with bounded parallelism."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from cagent.agent import AgentResult, run_agent
from cagent.memory import RunMemory
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
    memory: RunMemory | None = None,
) -> list[AgentResult]:
    """Run all tasks concurrently with bounded parallelism.

    Each task gets its own git worktree. Results are collected and returned
    in the same order as the input tasks list.
    """
    sem = asyncio.Semaphore(concurrency)
    results: dict[str, AgentResult] = {}
    lock = asyncio.Lock()

    async def _run_one(task: Task, stagger: int) -> None:
        # Stagger worktree creation to avoid concurrent git index.lock contention
        if stagger > 0:
            await asyncio.sleep(stagger * 0.3)
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

                # Build shared context from completed tasks
                shared_ctx = ""
                if memory:
                    async with lock:
                        completed_ids = [t.id for t in tasks if t.status in ("done", "noop")]
                    shared_ctx = memory.build_shared_context(completed_ids)

                # Run agent
                result = await run_agent(
                    task=task,
                    worktree_path=worktree_path,
                    run_dir=run_dir,
                    timeout=timeout,
                    model_override=worker_model_override,
                    dashboard=dashboard,
                    shared_context=shared_ctx,
                    memory=memory,
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

    # Use gather(return_exceptions=True) so a single task failure
    # does not cancel all other running tasks.
    gather_results = await asyncio.gather(
        *[_run_one(task, i) for i, task in enumerate(tasks)],
        return_exceptions=True,
    )

    # Check for unexpected exceptions that escaped _run_one's try/except
    for i, result in enumerate(gather_results):
        if isinstance(result, BaseException):
            logging.warning("Task %s gather returned exception: %s", tasks[i].id, result)

    # Return results in the same order as input tasks
    return [
        results.get(t.id, AgentResult(task_id=t.id, status="failed", fail_reason="task was cancelled"))
        for t in tasks
    ]
