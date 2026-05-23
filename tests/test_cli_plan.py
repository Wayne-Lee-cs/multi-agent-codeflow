"""Tests for cagent/cli/plan.py — directory scanning and plan command."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cagent.cli.plan import _cmd_plan, _scan_dir_tree


class TestScanDirTree:
    """Tests for _scan_dir_tree function."""

    def test_basic_directory(self, tmp_path: Path) -> None:
        """Should list files and directories."""
        (tmp_path / "file.txt").write_text("content")
        (tmp_path / "subdir").mkdir()

        result = _scan_dir_tree(tmp_path)
        assert "file.txt" in result
        assert "subdir/" in result

    def test_nested_directories(self, tmp_path: Path) -> None:
        """Should recurse into subdirectories."""
        (tmp_path / "sub" / "deep").mkdir(parents=True)
        (tmp_path / "sub" / "deep" / "file.py").write_text("# code")

        result = _scan_dir_tree(tmp_path, max_depth=3)
        assert "sub/" in result
        assert "deep/" in result
        assert "file.py" in result

    def test_max_depth_limit(self, tmp_path: Path) -> None:
        """Should not recurse past max_depth."""
        (tmp_path / "a" / "b" / "c").mkdir(parents=True)
        (tmp_path / "a" / "b" / "c" / "deep.txt").write_text("deep")

        result = _scan_dir_tree(tmp_path, max_depth=2)
        assert "a/" in result
        # b/ is at depth 2, so it should be included
        assert "b/" in result
        # c/ is at depth 3, should NOT be included (max_depth=2)
        assert "c/" not in result

    def test_skips_hidden_directories(self, tmp_path: Path) -> None:
        """Should skip directories starting with dot."""
        (tmp_path / ".hidden").mkdir()
        (tmp_path / ".git").mkdir()
        (tmp_path / "visible").mkdir()

        result = _scan_dir_tree(tmp_path)
        assert ".hidden" not in result
        assert ".git" not in result
        assert "visible/" in result

    def test_skips_special_directories(self, tmp_path: Path) -> None:
        """Should skip __pycache__, node_modules, .venv, etc."""
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "node_modules").mkdir()
        (tmp_path / ".venv").mkdir()
        (tmp_path / ".cagent").mkdir()
        (tmp_path / "src").mkdir()

        result = _scan_dir_tree(tmp_path)
        assert "__pycache__" not in result
        assert "node_modules" not in result
        assert ".venv" not in result
        assert ".cagent" not in result
        assert "src/" in result

    def test_sorted_output(self, tmp_path: Path) -> None:
        """Should sort entries: directories first, then alphabetically."""
        (tmp_path / "zebra.txt").write_text("")
        (tmp_path / "alpha.txt").write_text("")
        (tmp_path / "beta_dir").mkdir()
        (tmp_path / "alpha_dir").mkdir()

        result = _scan_dir_tree(tmp_path)
        lines = [l.strip() for l in result.split("\n") if l.strip()]

        # Directories should come before files
        dir_indices = [i for i, l in enumerate(lines) if l.endswith("/")]
        file_indices = [i for i, l in enumerate(lines) if not l.endswith("/")]

        if dir_indices and file_indices:
            assert max(dir_indices) < min(file_indices)

    def test_empty_directory(self, tmp_path: Path) -> None:
        """Should return empty string for empty directory."""
        result = _scan_dir_tree(tmp_path)
        assert result == ""

    def test_nonexistent_directory_handled(self, tmp_path: Path) -> None:
        """Should handle non-existent directory gracefully."""
        # Non-existent directory will raise FileNotFoundError
        result = _scan_dir_tree(tmp_path / "nonexistent")
        assert result == ""

    def test_indentation(self, tmp_path: Path) -> None:
        """Should indent nested entries."""
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "file.txt").write_text("")

        result = _scan_dir_tree(tmp_path, max_depth=3)
        lines = result.split("\n")

        # Find the nested file line
        for line in lines:
            if "file.txt" in line:
                # Should be indented (2 spaces per level)
                assert line.startswith("  ")
                break


def _make_plan_args(goal: str = "test goal", ref: str | None = None, model: str | None = None) -> argparse.Namespace:
    return argparse.Namespace(goal=goal, ref=ref, model=model)


class TestCmdPlan:
    """Tests for _cmd_plan function."""

    def test_success(self, tmp_path: Path) -> None:
        """Successful plan generates tasks.md and prints summary."""
        # Setup: create a git repo with tasks.md output
        (tmp_path / ".git").mkdir()
        tasks_content = """# Task Plan

## Tasks

### Task 001
- **depends_on**: none
- **files**: src/main.py

Create main module.
"""
        conv_content = "# Conventions\n\nUse Python 3.11+\n"

        def fake_run(cmd, **kwargs):
            # Simulate claude creating tasks.md and conventions.md
            (tmp_path / "tasks.md").write_text(tasks_content, encoding="utf-8")
            (tmp_path / "conventions.md").write_text(conv_content, encoding="utf-8")
            result = MagicMock()
            result.returncode = 0
            result.stdout = "Done"
            result.stderr = ""
            return result

        with patch("cagent.cli.plan._get_repo_root", return_value=tmp_path), \
             patch("cagent.cli.plan._preflight_check"), \
             patch("cagent.safety.prepare_sandbox"), \
             patch("cagent.agent._resolve_claude", return_value="claude"), \
             patch("cagent.cli.plan.subprocess.run", side_effect=fake_run), \
             patch("cagent.agent._CAGENT_GITIGNORE_MARKER", "[cagent-sandbox]"), \
             patch("cagent.agent._CAGENT_GITIGNORE_LINES", ".claude/\n"):
            _cmd_plan(_make_plan_args())

        # tasks.md should still exist (we don't clean it up)
        assert (tmp_path / "tasks.md").exists()

    def test_timeout(self, tmp_path: Path) -> None:
        """Timeout → sys.exit(1)."""
        (tmp_path / ".git").mkdir()

        def fake_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, 300)

        with patch("cagent.cli.plan._get_repo_root", return_value=tmp_path), \
             patch("cagent.cli.plan._preflight_check"), \
             patch("cagent.safety.prepare_sandbox"), \
             patch("cagent.agent._resolve_claude", return_value="claude"), \
             patch("cagent.cli.plan.subprocess.run", side_effect=fake_run), \
             patch("cagent.agent._CAGENT_GITIGNORE_MARKER", "[cagent-sandbox]"), \
             patch("cagent.agent._CAGENT_GITIGNORE_LINES", ".claude/\n"):
            with pytest.raises(SystemExit):
                _cmd_plan(_make_plan_args())

    def test_nonzero_exit(self, tmp_path: Path) -> None:
        """Non-zero exit code → sys.exit(1)."""
        (tmp_path / ".git").mkdir()

        def fake_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 1
            result.stdout = ""
            result.stderr = "Error: model not found"
            return result

        with patch("cagent.cli.plan._get_repo_root", return_value=tmp_path), \
             patch("cagent.cli.plan._preflight_check"), \
             patch("cagent.safety.prepare_sandbox"), \
             patch("cagent.agent._resolve_claude", return_value="claude"), \
             patch("cagent.cli.plan.subprocess.run", side_effect=fake_run), \
             patch("cagent.agent._CAGENT_GITIGNORE_MARKER", "[cagent-sandbox]"), \
             patch("cagent.agent._CAGENT_GITIGNORE_LINES", ".claude/\n"):
            with pytest.raises(SystemExit):
                _cmd_plan(_make_plan_args())

    def test_no_tasks_md_created(self, tmp_path: Path) -> None:
        """Architect agent doesn't create tasks.md → sys.exit(1)."""
        (tmp_path / ".git").mkdir()

        def fake_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stdout = "Done"
            result.stderr = ""
            return result

        with patch("cagent.cli.plan._get_repo_root", return_value=tmp_path), \
             patch("cagent.cli.plan._preflight_check"), \
             patch("cagent.safety.prepare_sandbox"), \
             patch("cagent.agent._resolve_claude", return_value="claude"), \
             patch("cagent.cli.plan.subprocess.run", side_effect=fake_run), \
             patch("cagent.agent._CAGENT_GITIGNORE_MARKER", "[cagent-sandbox]"), \
             patch("cagent.agent._CAGENT_GITIGNORE_LINES", ".claude/\n"):
            with pytest.raises(SystemExit):
                _cmd_plan(_make_plan_args())

    def test_sandbox_cleanup_on_success(self, tmp_path: Path) -> None:
        """Sandbox files are cleaned up after successful plan."""
        (tmp_path / ".git").mkdir()
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / "hooks").mkdir(parents=True)

        def fake_run(cmd, **kwargs):
            (tmp_path / "tasks.md").write_text(
                "# Task Plan\n\n## Tasks\n\n### Task 001\n- **depends_on**: none\n- **files**: a.py\n\nDo stuff\n",
                encoding="utf-8",
            )
            result = MagicMock()
            result.returncode = 0
            result.stdout = "Done"
            result.stderr = ""
            return result

        with patch("cagent.cli.plan._get_repo_root", return_value=tmp_path), \
             patch("cagent.cli.plan._preflight_check"), \
             patch("cagent.safety.prepare_sandbox"), \
             patch("cagent.agent._resolve_claude", return_value="claude"), \
             patch("cagent.cli.plan.subprocess.run", side_effect=fake_run), \
             patch("cagent.agent._CAGENT_GITIGNORE_MARKER", "[cagent-sandbox]"), \
             patch("cagent.agent._CAGENT_GITIGNORE_LINES", ".claude/\n"):
            _cmd_plan(_make_plan_args())

        # Sandbox files should be cleaned up
        assert not (tmp_path / ".claude" / "settings.local.json").exists()
        assert not (tmp_path / ".claude" / "hooks" / "cagent-guard.py").exists()
