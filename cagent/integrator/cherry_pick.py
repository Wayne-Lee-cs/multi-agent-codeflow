"""Cherry-pick integration strategy."""

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

__all__ = ["cherry_pick_strategy"]


async def cherry_pick_strategy(
    tasks: list[Task],
    worktree_path: Path,
    run_dir: Path,
    integrator_model_override: str | None,
    timeout: int,
    dashboard: Dashboard | None,
    memory: RunMemory | None,
    api_key: str | None = None,
) -> tuple[list[Task], list[Task]]:
    """Cherry-pick strategy: cherry-pick each task commit individually."""
    integrated: list[Task] = []
    failed: list[Task] = []
    for task in tasks:
        try:
            success = await _cherry_pick_one(
                task=task,
                integrated_tasks=integrated,
                worktree_path=worktree_path,
                run_dir=run_dir,
                integrator_model_override=integrator_model_override,
                timeout=timeout,
                dashboard=dashboard,
                memory=memory,
                api_key=api_key,
            )
        except Exception as e:
            success = False
            _report(dashboard, "error", f"cherry-pick task {task.id} exception: {e}")
        if success:
            integrated.append(task)
        else:
            failed.append(task)
            _report(dashboard, "error", f"cherry-pick task {task.id} failed, skipping")
    return integrated, failed


async def _cherry_pick_one(
    task: Task,
    integrated_tasks: list[Task],
    worktree_path: Path,
    run_dir: Path,
    integrator_model_override: str | None,
    timeout: int,
    dashboard: Dashboard | None,
    memory: RunMemory | None = None,
    api_key: str | None = None,
) -> bool:
    """Cherry-pick a single task commit. Returns True on success."""
    if not task.commit_sha:
        raise RuntimeError(f"task {task.id} has no commit_sha")

    await _run_git("checkout", "HEAD", "--", ".claude/", cwd=worktree_path, check=False)
    await _run_git("checkout", "HEAD", "--", ".gitignore", cwd=worktree_path, check=False)

    result = await _run_git("cherry-pick", task.commit_sha, cwd=worktree_path, check=False)
    if result.returncode == 0:
        return True

    status = await _run_git("status", "--porcelain", cwd=worktree_path, check=False)

    if not _has_conflict_markers(status.stdout):
        await _run_git("cherry-pick", "--abort", cwd=worktree_path, check=False)
        return False

    return await _resolve_conflicts(
        task=task,
        integrated_tasks=integrated_tasks,
        worktree_path=worktree_path,
        run_dir=run_dir,
        integrator_model_override=integrator_model_override,
        timeout=timeout,
        dashboard=dashboard,
        memory=memory,
        api_key=api_key,
    )
