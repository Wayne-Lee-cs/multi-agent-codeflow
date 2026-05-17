"""Async dispatcher — run tasks concurrently with bounded parallelism."""

from __future__ import annotations

import asyncio
import logging
import time
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
    conventions: str = "",
) -> list[AgentResult]:
    """Run all tasks concurrently with bounded parallelism.

    Supports dependency graph scheduling: tasks with depends_on are executed
    in waves, waiting for dependencies to complete before starting.

    Each task gets its own git worktree. Results are collected and returned
    in the same order as the input tasks list.
    """
    sem = asyncio.Semaphore(concurrency)
    results: dict[str, AgentResult] = {}
    lock = asyncio.Lock()
    _last_dump_time = 0.0
    _DUMP_THROTTLE = 1.0  # seconds

    def _throttled_dump() -> None:
        """Dump state at most once per second. Always dumps on final call."""
        nonlocal _last_dump_time
        now = time.monotonic()
        if now - _last_dump_time >= _DUMP_THROTTLE:
            dump_state(run_dir, tasks)
            _last_dump_time = now

    async def _run_one(task: Task, stagger: int) -> None:
        # Stagger worktree creation to avoid concurrent git index.lock contention.
        # Only the first `concurrency` tasks get staggered; subsequent waves
        # start immediately since the initial burst is past.
        if 0 < stagger < concurrency:
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
                        _throttled_dump()
                    return

                # Update task status
                async with lock:
                    task.status = "running"
                    _throttled_dump()

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
                    conventions=conventions,
                )

                # Update task with result
                async with lock:
                    task.status = result.status
                    task.commit_sha = result.commit_sha
                    results[task.id] = result
                    _throttled_dump()

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
                    _throttled_dump()

    # Check if any task has dependencies (wave-based scheduling)
    has_deps = any(t.depends_on for t in tasks)

    if has_deps:
        # Wave-based scheduling: execute tasks in dependency order
        task_map = {t.id: t for t in tasks}

        # Validate dependency graph
        for t in tasks:
            for dep_id in t.depends_on:
                if dep_id not in task_map:
                    raise ValueError(
                        f"Task {t.id} depends on non-existent task '{dep_id}'. "
                        f"Available tasks: {sorted(task_map.keys())}"
                    )

        # Detect cycles using Kahn's algorithm
        in_degree: dict[str, int] = {t.id: 0 for t in tasks}
        for t in tasks:
            for dep in t.depends_on:
                in_degree[t.id] += 1
        queue = [tid for tid, deg in in_degree.items() if deg == 0]
        visited = 0
        while queue:
            tid = queue.pop(0)
            visited += 1
            for t in tasks:
                if tid in t.depends_on:
                    in_degree[t.id] -= 1
                    if in_degree[t.id] == 0:
                        queue.append(t.id)
        if visited != len(tasks):
            cycle_tasks = [t.id for t in tasks if in_degree[t.id] > 0]
            raise ValueError(
                f"Circular dependency detected. Tasks involved: {cycle_tasks}"
            )

        completed: set[str] = set()
        failed: set[str] = set()
        stagger_counter = 0

        while True:
            # Find ready tasks: pending + all deps completed (not failed)
            ready = [
                t for t in tasks
                if t.status == "pending" and all(d in completed for d in t.depends_on)
            ]
            if not ready:
                break

            # Run this wave in parallel
            gather_results = await asyncio.gather(
                *[_run_one(task, stagger_counter + i) for i, task in enumerate(ready)],
                return_exceptions=True,
            )
            for i, result in enumerate(gather_results):
                if isinstance(result, BaseException):
                    logging.warning("Task %s gather returned exception: %s", ready[i].id, result)

            # Update completed set — only done/noop count as completed.
            # Failed deps block downstream (they are not added to completed).
            async with lock:
                completed = {t.id for t in tasks if t.status in ("done", "noop")}
                failed = {t.id for t in tasks if t.status == "failed"}
            stagger_counter += len(ready)

        # Mark tasks blocked by failed dependencies
        for t in tasks:
            if t.status == "pending":
                failed_deps = [d for d in t.depends_on if d in failed]
                if failed_deps:
                    async with lock:
                        t.status = "failed"
                        results[t.id] = AgentResult(
                            task_id=t.id,
                            status="failed",
                            fail_reason=f"blocked by failed dependency: {', '.join(failed_deps)}",
                        )
                        if dashboard:
                            dashboard.set_task_status(
                                t.id, "failed",
                                fail_reason=f"blocked by failed dependency: {', '.join(failed_deps)}",
                            )
                        dump_state(run_dir, tasks)
    else:
        # No dependencies: run all tasks concurrently (original behavior)
        gather_results = await asyncio.gather(
            *[_run_one(task, i) for i, task in enumerate(tasks)],
            return_exceptions=True,
        )
        for i, result in enumerate(gather_results):
            if isinstance(result, BaseException):
                logging.warning("Task %s gather returned exception: %s", tasks[i].id, result)

    # Final flush — ensure the complete state is persisted
    dump_state(run_dir, tasks)

    # Return results in the same order as input tasks
    return [
        results.get(t.id, AgentResult(task_id=t.id, status="failed", fail_reason="task was cancelled"))
        for t in tasks
    ]
