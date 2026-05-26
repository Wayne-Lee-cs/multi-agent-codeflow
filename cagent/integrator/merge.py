"""Merge integration strategy."""

from __future__ import annotations

from pathlib import Path

from cagent.memory import RunMemory
from cagent.progress import Dashboard
from cagent.tasks import Task

from .base import (
    _has_conflict_markers,
    _report,
    _resolve_conflicts,
    _run_git,
)

__all__ = ["merge_strategy"]


async def merge_strategy(
    tasks: list[Task],
    worktree_path: Path,
    run_dir: Path,
    integration_branch: str,
    run_id: str,
    integrator_model_override: str | None,
    timeout: int,
    dashboard: Dashboard | None,
    memory: RunMemory | None,
    api_key: str | None = None,
) -> tuple[list[Task], list[Task]]:
    """Merge strategy: merge each task branch into integration branch."""
    integrated = []
    failed = []
    temp_branches = []

    for task in tasks:
        if not task.commit_sha:
            failed.append(task)
            continue

        _report(dashboard, "text", f"merging task {task.id}...")

        task_branch = f"cagent/{run_id}/task-{task.id}"
        temp_branches.append(task_branch)
        try:
            await _run_git("branch", "-f", task_branch, task.commit_sha, cwd=worktree_path, check=False)

            result = await _run_git("merge", "--no-ff", task_branch, cwd=worktree_path, check=False)

            if result.returncode == 0:
                integrated.append(task)
                _report(dashboard, "text", f"task {task.id} merged successfully")
            else:
                status = await _run_git("status", "--porcelain", cwd=worktree_path, check=False)
                if _has_conflict_markers(status.stdout):
                    success = await _resolve_conflicts(
                        task=task,
                        integrated_tasks=integrated,
                        worktree_path=worktree_path,
                        run_dir=run_dir,
                        integrator_model_override=integrator_model_override,
                        timeout=timeout,
                        dashboard=dashboard,
                        memory=memory,
                        completion_mode="merge",
                        api_key=api_key,
                    )
                    if success:
                        integrated.append(task)
                    else:
                        failed.append(task)
                else:
                    failed.append(task)
                    await _run_git("merge", "--abort", cwd=worktree_path, check=False)
        except Exception as e:
            failed.append(task)
            _report(dashboard, "error", f"merge task {task.id} exception: {e}")

    for branch in temp_branches:
        await _run_git("branch", "-D", branch, cwd=worktree_path, check=False)

    return integrated, failed
