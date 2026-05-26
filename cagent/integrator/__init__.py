"""Integrator — cherry-pick/merge/rebase task commits into a unified integration branch."""

from __future__ import annotations

import os
import time
from pathlib import Path

from cagent.memory import RunMemory
from cagent.progress import Dashboard, Event
from cagent.tasks import Task

from .base import (
    _has_conflict_markers,
    _post_integrate_validate,
    _report,
    _resolve_conflicts,
    _run_claude_agent,
    _run_git,
    _run_shell_cmd,
    _validate_cmd_str,
)
from .cherry_pick import cherry_pick_strategy
from .merge import merge_strategy
from .rebase import rebase_strategy

__all__ = [
    "integrate",
    "_has_conflict_markers",
    "_post_integrate_validate",
    "_run_claude_agent",
    "_run_git",
    "_run_shell_cmd",
    "_validate_cmd_str",
]


async def integrate(
    tasks: list[Task],
    run_dir: Path,
    base_sha: str,
    repo_root: Path,
    squash: bool = False,
    integrator_model_override: str | None = None,
    timeout: int = 1800,
    dashboard: Dashboard | None = None,
    memory: RunMemory | None = None,
    post_integrate_cmd: str | None = None,
    strategy: str = "cherry-pick",
    api_key: str | None = None,
) -> str:
    """Integrate all done task commits into an integration branch.

    Supported strategies:
      - cherry-pick: cherry-pick each commit individually (default)
      - merge: merge each task branch into integration branch
      - rebase: rebase task branches onto integration branch

    Returns the integration branch tip SHA.
    """
    run_id = run_dir.name
    integration_branch = f"cagent/{run_id}/integration"
    worktree_path = repo_root / ".cagent" / "worktrees" / run_id / "_integration"

    from cagent.worktree import create_worktree
    create_worktree(repo_root, worktree_path, integration_branch, base_sha)

    done_tasks = [t for t in tasks if t.status == "done" and t.commit_sha]
    if not done_tasks:
        return base_sha

    valid_strategies = {"cherry-pick", "merge", "rebase"}
    if strategy not in valid_strategies:
        raise ValueError(f"Unknown strategy: {strategy!r}. Must be one of {valid_strategies}")

    if strategy == "merge":
        integrated, failed = await merge_strategy(
            tasks=done_tasks,
            worktree_path=worktree_path,
            run_dir=run_dir,
            integration_branch=integration_branch,
            run_id=run_id,
            integrator_model_override=integrator_model_override,
            timeout=timeout,
            dashboard=dashboard,
            memory=memory,
            api_key=api_key,
        )
    elif strategy == "rebase":
        integrated, failed = await rebase_strategy(
            tasks=done_tasks,
            worktree_path=worktree_path,
            run_dir=run_dir,
            integration_branch=integration_branch,
            integrator_model_override=integrator_model_override,
            timeout=timeout,
            dashboard=dashboard,
            memory=memory,
            api_key=api_key,
            run_id=run_id,
        )
    else:
        integrated, failed = await cherry_pick_strategy(
            tasks=done_tasks,
            worktree_path=worktree_path,
            run_dir=run_dir,
            integrator_model_override=integrator_model_override,
            timeout=timeout,
            dashboard=dashboard,
            memory=memory,
            api_key=api_key,
        )

    if failed and not integrated:
        raise RuntimeError(
            f"All {len(failed)} {strategy} integration attempts failed. "
            f"Worktree preserved at {worktree_path} for manual inspection."
        )

    if failed:
        if dashboard:
            event = Event(
                ts=time.time(),
                kind="text",
                summary=f"partial integration: {len(integrated)} ok, {len(failed)} skipped",
                raw={},
            )
            dashboard.update("_integrator", event)

    if post_integrate_cmd and integrated:
        validation_ok = await _post_integrate_validate(
            cmd_str=post_integrate_cmd,
            worktree_path=worktree_path,
            run_dir=run_dir,
            integrator_model_override=integrator_model_override,
            timeout=timeout,
            dashboard=dashboard,
            api_key=api_key,
        )
        if not validation_ok and dashboard:
            event = Event(
                ts=time.time(),
                kind="error",
                summary="post-integrate-cmd failed after 2 repair rounds — integration marked partial",
                raw={},
            )
            dashboard.update("_integrator", event)

    if squash:
        try:
            await _run_git("reset", "--soft", base_sha, cwd=worktree_path)
        except RuntimeError:
            await _run_git("reset", "--hard", base_sha, cwd=worktree_path, check=False)
            raise
        await _run_git("rm", "--cached", "-r", ".claude/", cwd=worktree_path, check=False)
        summary_parts = [f"task {t.id}: {t.prompt.split(chr(10))[0][:50]}" for t in integrated]
        commit_msg = "integrate:\n" + "\n".join(f"- {s}" for s in summary_parts)
        result = await _run_git("commit", "-m", commit_msg, cwd=worktree_path, check=False)
        if result.returncode != 0:
            reset_result = await _run_git("reset", "--hard", base_sha, cwd=worktree_path, check=False)
            reset_note = "Worktree reset to base." if reset_result.returncode == 0 else "WARNING: reset also failed — worktree state unknown."
            raise RuntimeError(
                f"Squash commit failed (exit {result.returncode}): {result.stderr.strip()[:200]}. "
                f"{reset_note} Preserved at {worktree_path} for inspection."
            )

    result = await _run_git("rev-parse", "HEAD", cwd=worktree_path)
    return result.stdout.strip()
