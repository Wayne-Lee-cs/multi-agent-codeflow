"""Git worktree creation, removal, and utilities."""

from __future__ import annotations

from pathlib import Path

from cagent.git_utils import run_git as _git


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
