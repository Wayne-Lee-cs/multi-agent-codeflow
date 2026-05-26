"""Integration tests for cagent/worktree.py — worktree creation and removal."""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from cagent.worktree import (
    cleanup_orphan_worktrees,
    create_worktree,
    current_head,
    delete_branch,
    detect_orphan_worktrees,
    remove_worktree,
)


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


class TestDetectOrphanWorktrees:
    def test_no_worktrees_returns_empty(self, tmp_repo):
        assert detect_orphan_worktrees(tmp_repo) == []

    def test_detects_orphan_with_no_pid(self, tmp_repo):
        run_id = "test-orphan-run"
        wt_path = tmp_repo / ".cagent" / "worktrees" / run_id / "task-001"
        create_worktree(tmp_repo, wt_path, "cagent/orphan/task-001", current_head(tmp_repo))

        orphans = detect_orphan_worktrees(tmp_repo)
        assert len(orphans) == 1
        assert orphans[0][0] == wt_path
        assert orphans[0][1] == run_id

        # Clean up
        remove_worktree(tmp_repo, wt_path)
        delete_branch(tmp_repo, "cagent/orphan/task-001")

    def test_skips_active_pid(self, tmp_repo):
        import os
        run_id = "test-active-run"
        wt_path = tmp_repo / ".cagent" / "worktrees" / run_id / "task-001"
        create_worktree(tmp_repo, wt_path, "cagent/active/task-001", current_head(tmp_repo))

        # Write a PID file with our own PID (active)
        pids_dir = tmp_repo / ".cagent" / "runs" / run_id / "pids"
        pids_dir.mkdir(parents=True)
        (pids_dir / "task-001.pid").write_text(str(os.getpid()), encoding="utf-8")

        orphans = detect_orphan_worktrees(tmp_repo)
        assert len(orphans) == 0

        # Clean up
        remove_worktree(tmp_repo, wt_path)
        delete_branch(tmp_repo, "cagent/active/task-001")

    def test_detects_orphan_with_dead_pid(self, tmp_repo):
        run_id = "test-dead-run"
        wt_path = tmp_repo / ".cagent" / "worktrees" / run_id / "task-001"
        create_worktree(tmp_repo, wt_path, "cagent/dead/task-001", current_head(tmp_repo))

        pids_dir = tmp_repo / ".cagent" / "runs" / run_id / "pids"
        pids_dir.mkdir(parents=True)
        (pids_dir / "task-001.pid").write_text("999999999", encoding="utf-8")

        with patch("cagent.worktree._is_pid_active", return_value=False):
            orphans = detect_orphan_worktrees(tmp_repo)
        assert len(orphans) == 1

        # Clean up
        remove_worktree(tmp_repo, wt_path)
        delete_branch(tmp_repo, "cagent/dead/task-001")


class TestCleanupOrphanWorktrees:
    def test_cleans_orphans(self, tmp_repo):
        run_id = "test-cleanup-run"
        wt_path = tmp_repo / ".cagent" / "worktrees" / run_id / "task-001"
        create_worktree(tmp_repo, wt_path, "cagent/cleanup/task-001", current_head(tmp_repo))
        assert wt_path.exists()

        cleaned = cleanup_orphan_worktrees(tmp_repo)
        assert cleaned == 1
        assert not wt_path.exists()

    def test_returns_zero_when_no_orphans(self, tmp_repo):
        assert cleanup_orphan_worktrees(tmp_repo) == 0
