"""Git worktree creation, removal, and utilities."""

from __future__ import annotations

import subprocess
from pathlib import Path


def _git(*args: str, cwd: str | Path | None = None) -> subprocess.CompletedProcess:
    """Run a git command, raising on failure with stderr details."""
    try:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
    except FileNotFoundError:
        raise RuntimeError("'git' not found in PATH. Please install Git.")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"git {' '.join(args)} failed (exit {e.returncode}): {e.stderr.strip()}"
        ) from e


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
