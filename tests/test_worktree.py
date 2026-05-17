"""Integration tests for cagent/worktree.py — worktree creation and removal."""

import subprocess
from pathlib import Path

import pytest

from cagent.worktree import create_worktree, current_head, delete_branch, remove_worktree


class TestCurrentHead:
    def test_returns_sha(self, tmp_repo):
        sha = current_head(tmp_repo)
        assert len(sha) >= 7
        # Verify it's a valid hex string
        int(sha, 16)

    def test_returns_latest_commit(self, tmp_repo):
        sha = current_head(tmp_repo)
        # Verify with git directly
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=tmp_repo, capture_output=True, text=True,
        )
        assert sha == result.stdout.strip()


class TestCreateWorktree:
    def test_creates_worktree_directory(self, tmp_repo):
        wt_path = tmp_repo / ".cagent" / "worktrees" / "test" / "task-001"
        create_worktree(tmp_repo, wt_path, "cagent/test/task-001", current_head(tmp_repo))
        assert wt_path.exists()
        assert (wt_path / ".git").exists() or (wt_path / ".git").is_file()

    def test_creates_branch(self, tmp_repo):
        wt_path = tmp_repo / ".cagent" / "worktrees" / "test" / "task-001"
        create_worktree(tmp_repo, wt_path, "cagent/test/task-001", current_head(tmp_repo))
        # Verify branch exists
        result = subprocess.run(
            ["git", "branch", "--list", "cagent/test/task-001"],
            cwd=tmp_repo, capture_output=True, text=True,
        )
        assert "cagent/test/task-001" in result.stdout

    def test_worktree_at_correct_sha(self, tmp_repo):
        base_sha = current_head(tmp_repo)
        wt_path = tmp_repo / ".cagent" / "worktrees" / "test" / "task-001"
        create_worktree(tmp_repo, wt_path, "cagent/test/task-001", base_sha)
        wt_sha = current_head(wt_path)
        assert wt_sha == base_sha


class TestRemoveWorktree:
    def test_removes_directory(self, tmp_repo):
        wt_path = tmp_repo / ".cagent" / "worktrees" / "test" / "task-001"
        create_worktree(tmp_repo, wt_path, "cagent/test/task-001", current_head(tmp_repo))
        assert wt_path.exists()
        remove_worktree(tmp_repo, wt_path)
        assert not wt_path.exists()


class TestDeleteBranch:
    def test_deletes_branch(self, tmp_repo):
        wt_path = tmp_repo / ".cagent" / "worktrees" / "test" / "task-001"
        create_worktree(tmp_repo, wt_path, "cagent/test/task-001", current_head(tmp_repo))
        remove_worktree(tmp_repo, wt_path)
        delete_branch(tmp_repo, "cagent/test/task-001")
        result = subprocess.run(
            ["git", "branch", "--list", "cagent/test/task-001"],
            cwd=tmp_repo, capture_output=True, text=True,
        )
        assert result.stdout.strip() == ""


class TestWorktreeIsolation:
    def test_changes_in_worktree_dont_affect_main(self, tmp_repo):
        wt_path = tmp_repo / ".cagent" / "worktrees" / "test" / "task-001"
        create_worktree(tmp_repo, wt_path, "cagent/test/task-001", current_head(tmp_repo))

        # Write a file in the worktree
        (wt_path / "new_file.txt").write_text("worktree content", encoding="utf-8")

        # Verify it doesn't exist in the main repo
        assert not (tmp_repo / "new_file.txt").exists()

        # Clean up
        remove_worktree(tmp_repo, wt_path)
        delete_branch(tmp_repo, "cagent/test/task-001")
