"""Rebase integration strategy."""

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

__all__ = ["rebase_strategy"]


async def rebase_strategy(
    tasks: list[Task],
    worktree_path: Path,
    run_dir: Path,
    integration_branch: str,
    integrator_model_override: str | None,
    timeout: int,
    dashboard: Dashboard | None,
    memory: RunMemory | None,
    api_key: str | None = None,
    run_id: str = "",
) -> tuple[list[Task], list[Task]]:
    """Rebase strategy: replay task commits onto integration branch.

    Internally uses cherry-pick (not git rebase), which is equivalent
    to a "replay" strategy. For single-commit branches this behaves identically
    to rebase; for multi-commit branches, each commit is replayed independently.
    """
    integrated = []
    failed = []

    task_commits = [(task, task.commit_sha) for task in tasks if task.commit_sha]
    if not task_commits:
        return [], list(tasks)

    if not run_id:
        raise ValueError("run_id is required for rebase_strategy")
    temp_branch = f"cagent/{run_id}/temp-rebase"
    try:
        await _run_git("checkout", "-b", temp_branch, cwd=worktree_path, check=True)

        for task, sha in task_commits:
            _report(dashboard, "text", f"rebasing task {task.id}...")

            result = await _run_git("cherry-pick", sha, cwd=worktree_path, check=False)

            if result.returncode == 0:
                integrated.append(task)
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
                        completion_mode="rebase",
                        api_key=api_key,
                    )
                    if success:
                        integrated.append(task)
                    else:
                        failed.append(task)
                else:
                    failed.append(task)
                    await _run_git("cherry-pick", "--abort", cwd=worktree_path, check=False)

        result = await _run_git("rev-parse", "HEAD", cwd=worktree_path, check=False)
        temp_sha = result.stdout.strip()
        await _run_git("branch", "-f", integration_branch, temp_sha, cwd=worktree_path, check=False)
        await _run_git("checkout", integration_branch, cwd=worktree_path, check=False)

    except Exception as e:
        _report(dashboard, "error", f"rebase strategy exception: {e}")
        failed = [t for t in tasks if t not in integrated]
    finally:
        await _run_git("checkout", integration_branch, cwd=worktree_path, check=False)
        await _run_git("branch", "-D", temp_branch, cwd=worktree_path, check=False)

    return integrated, failed
