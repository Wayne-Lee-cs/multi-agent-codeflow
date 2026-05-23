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


# --- _run_git tests ---


@pytest.mark.asyncio
async def test_run_git_success(tmp_path: Path) -> None:
    proc = _make_process(returncode=0, stdout=b"output\n")

    with patch("cagent.integrator.asyncio.create_subprocess_exec", return_value=proc):
        result = await _run_git("status", cwd=tmp_path)

    assert result.returncode == 0
    assert result.stdout == "output\n"


@pytest.mark.asyncio
async def test_run_git_failure_raises(tmp_path: Path) -> None:
    proc = _make_process(returncode=1, stderr=b"error\n")

    with patch("cagent.integrator.asyncio.create_subprocess_exec", return_value=proc):
        with pytest.raises(RuntimeError, match="failed"):
            await _run_git("cherry-pick", "abc", cwd=tmp_path)


@pytest.mark.asyncio
async def test_run_git_check_false(tmp_path: Path) -> None:
    proc = _make_process(returncode=1, stderr=b"error\n")

    with patch("cagent.integrator.asyncio.create_subprocess_exec", return_value=proc):
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
         patch("cagent.integrator.asyncio.create_subprocess_exec", side_effect=mock_exec):
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
         patch("cagent.integrator.asyncio.create_subprocess_exec", side_effect=mock_exec):
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
         patch("cagent.integrator.asyncio.create_subprocess_exec", side_effect=mock_exec):
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
         patch("cagent.integrator.asyncio.create_subprocess_exec", side_effect=mock_exec):
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
    with patch("cagent.integrator.asyncio.create_subprocess_exec", return_value=proc):
        code, output = await _run_shell_cmd("echo ok", tmp_path)
    assert code == 0
    assert "ok" in output


@pytest.mark.asyncio
async def test_run_shell_cmd_failure(tmp_path: Path) -> None:
    """Failed command returns nonzero code."""
    proc = _make_process(returncode=1, stdout=b"error details\n")
    with patch("cagent.integrator.asyncio.create_subprocess_exec", return_value=proc):
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

    with patch("cagent.integrator.asyncio.create_subprocess_exec", return_value=proc):
        code, output = await _run_shell_cmd("sleep 999", tmp_path, timeout=1)
    assert code == 1
    assert "timed out" in output.lower()


# --- _post_integrate_validate tests ---


@pytest.mark.asyncio
async def test_post_validate_passes_first_round(tmp_path: Path) -> None:
    """Command passes on first round → returns True."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    with patch("cagent.integrator._run_shell_cmd", return_value=(0, "all tests pass")):
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

    with patch("cagent.integrator._run_shell_cmd", side_effect=mock_shell_cmd), \
         patch("cagent.integrator.prepare_sandbox"), \
         patch("cagent.integrator._resolve_claude", return_value="claude"), \
         patch("cagent.integrator.asyncio.create_subprocess_exec", side_effect=mock_exec):
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

    with patch("cagent.integrator._run_shell_cmd", return_value=(1, "FAIL")), \
         patch("cagent.integrator.prepare_sandbox"), \
         patch("cagent.integrator._resolve_claude", return_value="claude"), \
         patch("cagent.integrator.asyncio.create_subprocess_exec", side_effect=mock_exec):
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

    with patch("cagent.integrator._run_shell_cmd", return_value=(1, "FAIL")), \
         patch("cagent.integrator.prepare_sandbox"), \
         patch("cagent.integrator._resolve_claude", return_value="claude"), \
         patch("cagent.integrator.asyncio.create_subprocess_exec", return_value=repair_proc):
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
         patch("cagent.integrator.prepare_sandbox"), \
         patch("cagent.integrator._resolve_claude", return_value="claude"), \
         patch("cagent.integrator.asyncio.create_subprocess_exec", side_effect=mock_exec):
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
         patch("cagent.integrator.asyncio.create_subprocess_exec", side_effect=mock_exec):
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
         patch("cagent.integrator.asyncio.create_subprocess_exec", side_effect=mock_exec):
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
         patch("cagent.integrator.prepare_sandbox"), \
         patch("cagent.integrator._resolve_claude", return_value="claude"), \
         patch("cagent.integrator.asyncio.create_subprocess_exec", side_effect=mock_exec):
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
         patch("cagent.integrator.prepare_sandbox"), \
         patch("cagent.integrator._resolve_claude", return_value="claude"), \
         patch("cagent.integrator.asyncio.create_subprocess_exec", side_effect=mock_exec):
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
         patch("cagent.integrator.asyncio.create_subprocess_exec", side_effect=mock_exec):
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
        # Pipes are allowed (user may want to pipe output)
        assert _validate_cmd_str("pytest | tee output.txt") is True

    def test_command_with_semicolons(self) -> None:
        from cagent.integrator import _validate_cmd_str
        # Semicolons are allowed (user may chain commands)
        assert _validate_cmd_str("pytest; echo done") is True

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

        with patch("cagent.integrator._resolve_claude", return_value="claude"), \
             patch("cagent.integrator.asyncio.create_subprocess_exec", return_value=proc):
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

        with patch("cagent.integrator._resolve_claude", return_value="claude"), \
             patch("cagent.integrator.asyncio.create_subprocess_exec", return_value=proc):
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

        with patch("cagent.integrator._resolve_claude", return_value="claude"), \
             patch("cagent.integrator.asyncio.create_subprocess_exec",
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

        with patch("cagent.integrator._resolve_claude", return_value="claude"), \
             patch("cagent.integrator.asyncio.create_subprocess_exec",
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
