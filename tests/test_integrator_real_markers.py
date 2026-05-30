"""Real-git regression tests for conflict-marker detection in the integrator.

These tests deliberately use a *real* git repository (no mocking of git) so they
exercise the actual `git grep` conflict-marker scan in `_resolve_conflicts`.
They guard against the over-mocking blind spot that previously hid a false
positive: a legitimate file containing a line of seven-or-more '=' characters
(e.g. a markdown setext heading or an ASCII banner) used to match the marker
regex and abort an otherwise-successful conflict resolution.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from cagent.integrator.base import _resolve_conflicts
from cagent.tasks import Task


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )


def _make_conflict_repo(root: Path) -> str:
    """Create a repo with an in-progress cherry-pick conflict.

    Returns the SHA of the commit being cherry-picked (so the caller can drive
    `cherry-pick --continue` via _resolve_conflicts).
    """
    _git("init", cwd=root)
    _git("config", "user.email", "t@t", cwd=root)
    _git("config", "user.name", "t", cwd=root)
    _git("config", "commit.gpgsign", "false", cwd=root)

    (root / "file.py").write_text("base\n", encoding="utf-8")
    _git("add", "-A", cwd=root)
    _git("commit", "-m", "base", cwd=root)

    # Branch "feature" changes file.py one way.
    _git("checkout", "-b", "feature", cwd=root)
    (root / "file.py").write_text("feature change\n", encoding="utf-8")
    _git("add", "-A", cwd=root)
    _git("commit", "-m", "feature", cwd=root)
    feature_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(root),
        check=True, capture_output=True, text=True,
    ).stdout.strip()

    # Back on the integration branch, change file.py a conflicting way.
    _git("checkout", "master" if _has_master(root) else "main", cwd=root)
    (root / "file.py").write_text("integration change\n", encoding="utf-8")
    _git("add", "-A", cwd=root)
    _git("commit", "-m", "integration", cwd=root)

    # Now cherry-pick feature → conflict (do NOT use check=True, it exits 1).
    subprocess.run(
        ["git", "cherry-pick", feature_sha],
        cwd=str(root), capture_output=True, text=True,
    )
    return feature_sha


def _has_master(root: Path) -> bool:
    out = subprocess.run(
        ["git", "branch", "--list", "master"],
        cwd=str(root), capture_output=True, text=True,
    )
    return bool(out.stdout.strip())


@pytest.mark.asyncio
async def test_legit_equals_line_does_not_abort_resolution(tmp_path, monkeypatch):
    """A legit file with a '=======' line must not be flagged as a conflict marker.

    Reproduces the false positive: previously the grep regex matched bare
    `=======` and aborted the resolution even though the agent resolved the real
    conflict cleanly.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_conflict_repo(repo)
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    async def fake_agent(*args, **kwargs):
        # Simulate the integrator agent resolving the real conflict cleanly...
        (repo / "file.py").write_text("resolved\n", encoding="utf-8")
        # ...and a legitimate README with a markdown setext heading underline
        # (a line of '=' characters) that must NOT be treated as a conflict marker.
        (repo / "README.md").write_text("Title\n=======\nbody\n", encoding="utf-8")
        return 0

    monkeypatch.setattr("cagent.integrator.base._run_claude_agent", fake_agent)
    # prepare_sandbox writes into .claude/; keep it real but harmless in tmp repo.

    result = await _resolve_conflicts(
        task=Task(id="t1", prompt="do the thing", branch="cagent/t1"),
        integrated_tasks=[],
        worktree_path=repo,
        run_dir=run_dir,
        integrator_model_override=None,
        timeout=60,
        dashboard=None,
        completion_mode="cherry-pick",
    )

    assert result is True, "legit '=======' line falsely aborted conflict resolution"
    # The cherry-pick should have been completed (no in-progress state left).
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(repo),
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert head  # a commit exists
    # No CHERRY_PICK_HEAD means the cherry-pick was concluded, not dangling.
    assert not (repo / ".git" / "CHERRY_PICK_HEAD").exists()


@pytest.mark.asyncio
async def test_remaining_real_markers_still_abort(tmp_path, monkeypatch):
    """If the agent leaves real <<<<<<< / >>>>>>> markers, resolution must abort."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_conflict_repo(repo)
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    async def fake_agent(*args, **kwargs):
        # Agent "fails": leaves genuine conflict markers behind.
        (repo / "file.py").write_text(
            "<<<<<<< HEAD\nintegration change\n=======\nfeature change\n>>>>>>> x\n",
            encoding="utf-8",
        )
        return 0

    monkeypatch.setattr("cagent.integrator.base._run_claude_agent", fake_agent)

    result = await _resolve_conflicts(
        task=Task(id="t1", prompt="do the thing", branch="cagent/t1"),
        integrated_tasks=[],
        worktree_path=repo,
        run_dir=run_dir,
        integrator_model_override=None,
        timeout=60,
        dashboard=None,
        completion_mode="cherry-pick",
    )

    assert result is False, "real conflict markers must abort resolution"
