"""Git worktree creation, removal, and utilities."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from cagent.compat import is_pid_active as _is_pid_active
from cagent.git_utils import run_git as _git

_log = logging.getLogger(__name__)


def current_head(repo_root: str | Path) -> str:
    """Return the current HEAD SHA."""
    result = _git("rev-parse", "HEAD", cwd=repo_root)
    return result.stdout.strip()


def create_worktree(
    repo_root: str | Path,
    worktree_path: str | Path,
    branch: str,
    base_sha: str,
) -> None:
    """Create a new git worktree with a fresh branch from base_sha."""
    worktree_path = Path(worktree_path)
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    _git(
        "worktree", "add",
        "-b", branch,
        str(worktree_path),
        base_sha,
        cwd=repo_root,
    )


def remove_worktree(repo_root: str | Path, worktree_path: str | Path) -> None:
    """Force-remove a git worktree directory."""
    _git("worktree", "remove", "--force", str(worktree_path), cwd=repo_root)


def delete_branch(repo_root: str | Path, branch: str) -> None:
    """Delete a local branch (force)."""
    _git("branch", "-D", branch, cwd=repo_root)


def detect_orphan_worktrees(repo_root: str | Path) -> list[tuple[Path, str]]:
    """Detect orphaned worktrees that have no active cagent process.

    Returns list of (worktree_path, run_id) for orphaned worktrees.
    A worktree is orphaned if:
      - It exists under .cagent/worktrees/
      - Its run has no active PID files (all processes finished/crashed)
    """
    repo_root = Path(repo_root)
    worktrees_root = repo_root / ".cagent" / "worktrees"
    runs_dir = repo_root / ".cagent" / "runs"

    if not worktrees_root.exists():
        return []

    orphans: list[tuple[Path, str]] = []

    for run_dir in sorted(worktrees_root.iterdir()):
        if not run_dir.is_dir():
            continue
        run_id = run_dir.name

        has_active_pid = False
        pids_dir = runs_dir / run_id / "pids"
        if pids_dir.exists():
            for pid_file in pids_dir.glob("*.pid"):
                try:
                    pid = int(pid_file.read_text(encoding="utf-8").strip())
                    if _is_pid_active(pid):
                        has_active_pid = True
                        break
                except (ValueError, OSError):
                    pass

        if not has_active_pid:
            for wt in sorted(run_dir.iterdir()):
                if wt.is_dir():
                    orphans.append((wt, run_id))

    return orphans


def cleanup_orphan_worktrees(
    repo_root: str | Path,
    orphans: list[tuple[Path, str]] | None = None,
) -> int:
    """Remove orphaned worktrees and their branches.

    Returns the number of worktrees cleaned up.
    """
    repo_root = Path(repo_root)
    if orphans is None:
        orphans = detect_orphan_worktrees(repo_root)

    if not orphans:
        return 0

    cleaned = 0
    run_ids: set[str] = set()
    for wt_path, run_id in orphans:
        run_ids.add(run_id)
        try:
            _git("worktree", "remove", "--force", str(wt_path), cwd=repo_root, check=False)
            cleaned += 1
        except Exception:
            _log.debug("Failed to remove orphan worktree: %s", wt_path)

    # Delete the now-unreferenced task branches so they don't accumulate.
    for run_id in run_ids:
        try:
            result = _git("branch", "--list", f"cagent/{run_id}/*", cwd=repo_root, check=False)
        except Exception:
            continue
        for line in result.stdout.splitlines():
            branch = line.strip().removeprefix("* ").strip()
            if branch:
                _git("branch", "-D", branch, cwd=repo_root, check=False)

    # Clean up empty run directories under worktrees/
    worktrees_root = repo_root / ".cagent" / "worktrees"
    if worktrees_root.exists():
        for run_dir in list(worktrees_root.iterdir()):
            if run_dir.is_dir():
                try:
                    if not any(run_dir.iterdir()):
                        run_dir.rmdir()
                except OSError:
                    pass

    return cleaned
