"""Tests for cagent.agent — mock subprocess tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cagent.agent import AgentResult, _run_git_async, run_agent
from cagent.git_utils import GitTimeoutError
from cagent.tasks import Task

from tests.conftest import AsyncLineIterator, _make_process


def _git_mock_exec(
    claude_proc: AsyncMock,
    git_status: AsyncMock | None = None,
    git_checkout: AsyncMock | None = None,
    git_add: AsyncMock | None = None,
    git_commit: AsyncMock | None = None,
    git_sha: AsyncMock | None = None,
):
    """Create a mock_exec function for create_subprocess_exec."""
    default = _make_process(returncode=0)

    async def mock_exec(*args, **kwargs):
        cmd = args[0] if args else ""
        if cmd == "claude":
            return claude_proc
        git_cmd = args[1] if len(args) > 1 else ""
        if git_cmd == "status" and git_status:
            return git_status
        if git_cmd == "checkout" and git_checkout:
            return git_checkout
        if git_cmd == "add" and git_add:
            return git_add
        if git_cmd == "commit" and git_commit:
            return git_commit
        if git_cmd == "rev-parse" and git_sha:
            return git_sha
        return default

    return mock_exec


@pytest.mark.asyncio
async def test_run_agent_success(
    sample_task: Task, tmp_worktree: Path, tmp_run_dir: Path,
) -> None:
    """Normal completion → commit → done."""
    claude_proc = _make_process(
        returncode=0,
        stdout_lines=[b'{"type":"result","usage":{"input_tokens":10,"output_tokens":20}}\n'],
    )
    git_status = _make_process(returncode=0, stdout=b" M file.py\n")
    git_checkout = _make_process(returncode=0)
    git_add = _make_process(returncode=0)
    git_commit = _make_process(returncode=0)
    git_sha = _make_process(returncode=0, stdout=b"abc123\n")

    mock_exec = _git_mock_exec(
        claude_proc, git_status, git_checkout, git_add, git_commit, git_sha,
    )

    with patch("cagent.agent._resolve_claude", return_value="claude"), \
         patch("cagent.agent.asyncio.create_subprocess_exec", side_effect=mock_exec), \
         patch("cagent.agent.prepare_sandbox"):
        result = await run_agent(
            task=sample_task, worktree_path=tmp_worktree, run_dir=tmp_run_dir,
        )

    assert result.status == "done"
    assert result.commit_sha == "abc123"


@pytest.mark.asyncio
async def test_run_agent_timeout(
    sample_task: Task, tmp_worktree: Path, tmp_run_dir: Path,
) -> None:
    """Timeout → terminate → kill → failed."""
    claude_proc = _make_process(returncode=0)

    async def hang_forever():
        while True:
            await asyncio.sleep(999)
            yield b""

    claude_proc.stdout = AsyncMock()
    claude_proc.stdout.__aiter__ = MagicMock(return_value=hang_forever())

    mock_exec = _git_mock_exec(claude_proc)

    with patch("cagent.agent._resolve_claude", return_value="claude"), \
         patch("cagent.agent.asyncio.create_subprocess_exec", side_effect=mock_exec), \
         patch("cagent.agent.prepare_sandbox"):
        result = await run_agent(
            task=sample_task, worktree_path=tmp_worktree,
            run_dir=tmp_run_dir, timeout=1,
        )

    assert result.status == "failed"
    assert "timeout" in result.fail_reason


@pytest.mark.asyncio
async def test_run_agent_nonzero_exit(
    sample_task: Task, tmp_worktree: Path, tmp_run_dir: Path,
) -> None:
    """Non-zero exit code → failed + fail_reason contains stderr."""
    claude_proc = _make_process(returncode=1, stdout_lines=[b"error line\n"])

    mock_exec = _git_mock_exec(claude_proc)

    with patch("cagent.agent._resolve_claude", return_value="claude"), \
         patch("cagent.agent.asyncio.create_subprocess_exec", side_effect=mock_exec), \
         patch("cagent.agent.prepare_sandbox"):
        result = await run_agent(
            task=sample_task, worktree_path=tmp_worktree, run_dir=tmp_run_dir,
        )

    assert result.status == "failed"
    assert "exited with code 1" in result.fail_reason


@pytest.mark.asyncio
async def test_run_agent_claude_not_found(
    sample_task: Task, tmp_worktree: Path, tmp_run_dir: Path,
) -> None:
    """claude CLI not found → failed."""
    async def mock_exec(*args, **kwargs):
        raise FileNotFoundError("claude not found")

    with patch("cagent.agent._resolve_claude", return_value="claude"), \
         patch("cagent.agent.asyncio.create_subprocess_exec", side_effect=mock_exec), \
         patch("cagent.agent.prepare_sandbox"):
        result = await run_agent(
            task=sample_task, worktree_path=tmp_worktree, run_dir=tmp_run_dir,
        )

    assert result.status == "failed"
    assert "not found" in result.fail_reason


@pytest.mark.asyncio
async def test_run_agent_commit_failure(
    sample_task: Task, tmp_worktree: Path, tmp_run_dir: Path,
) -> None:
    """git commit fails → failed."""
    claude_proc = _make_process(returncode=0, stdout_lines=[])
    git_status = _make_process(returncode=0, stdout=b" M file.py\n")
    git_commit = _make_process(returncode=1, stderr=b"nothing to commit\n")

    mock_exec = _git_mock_exec(
        claude_proc, git_status=git_status, git_commit=git_commit,
    )

    with patch("cagent.agent._resolve_claude", return_value="claude"), \
         patch("cagent.agent.asyncio.create_subprocess_exec", side_effect=mock_exec), \
         patch("cagent.agent.prepare_sandbox"):
        result = await run_agent(
            task=sample_task, worktree_path=tmp_worktree, run_dir=tmp_run_dir,
        )

    assert result.status == "failed"
    assert "commit failed" in result.fail_reason


@pytest.mark.asyncio
async def test_run_agent_no_changes(
    sample_task: Task, tmp_worktree: Path, tmp_run_dir: Path,
) -> None:
    """No changes in worktree → noop."""
    claude_proc = _make_process(returncode=0, stdout_lines=[])
    git_status = _make_process(returncode=0, stdout=b"")

    mock_exec = _git_mock_exec(claude_proc, git_status=git_status)

    with patch("cagent.agent._resolve_claude", return_value="claude"), \
         patch("cagent.agent.asyncio.create_subprocess_exec", side_effect=mock_exec), \
         patch("cagent.agent.prepare_sandbox"):
        result = await run_agent(
            task=sample_task, worktree_path=tmp_worktree, run_dir=tmp_run_dir,
        )

    assert result.status == "noop"


@pytest.mark.asyncio
async def test_run_agent_conventions_injected(
    sample_task: Task, tmp_worktree: Path, tmp_run_dir: Path,
) -> None:
    """Conventions and shared_context are injected into prompt."""
    class StdinCapture:
        def __init__(self):
            self.data = b""

        def write(self, data: bytes):
            self.data += data

        async def drain(self):
            pass

        def close(self):
            pass

        async def wait_closed(self):
            pass

    stdin_capture = StdinCapture()
    claude_proc = _make_process(returncode=0, stdout_lines=[])
    claude_proc.stdin = stdin_capture

    mock_exec = _git_mock_exec(claude_proc)

    with patch("cagent.agent._resolve_claude", return_value="claude"), \
         patch("cagent.agent.asyncio.create_subprocess_exec", side_effect=mock_exec), \
         patch("cagent.agent.prepare_sandbox"):
        await run_agent(
            task=sample_task, worktree_path=tmp_worktree,
            run_dir=tmp_run_dir,
            conventions="Use Python 3.11+",
            shared_context="Previous task completed.",
        )

    prompt = stdin_capture.data.decode("utf-8")
    assert "[Global Conventions]" in prompt
    assert "Use Python 3.11+" in prompt
    assert "[Shared context from previous tasks]" in prompt
    assert "Previous task completed." in prompt
    assert "[Your task]" in prompt
    assert "Test task prompt" in prompt


@pytest.mark.asyncio
async def test_run_agent_os_error(
    sample_task: Task, tmp_worktree: Path, tmp_run_dir: Path,
) -> None:
    """OSError launching process → failed."""
    async def mock_exec(*args, **kwargs):
        raise OSError("permission denied")

    with patch("cagent.agent._resolve_claude", return_value="claude"), \
         patch("cagent.agent.asyncio.create_subprocess_exec", side_effect=mock_exec), \
         patch("cagent.agent.prepare_sandbox"):
        result = await run_agent(
            task=sample_task, worktree_path=tmp_worktree, run_dir=tmp_run_dir,
        )

    assert result.status == "failed"
    assert "failed to launch" in result.fail_reason


@pytest.mark.asyncio
async def test_run_agent_max_turns_passed(
    sample_task: Task, tmp_worktree: Path, tmp_run_dir: Path,
) -> None:
    """max_turns is appended as --max-turns to claude -p command."""
    captured_cmds: list[list[str]] = []

    claude_proc = _make_process(returncode=0, stdout_lines=[])
    git_status = _make_process(returncode=0, stdout=b"")

    async def mock_exec(*args, **kwargs):
        captured_cmds.append(list(args))
        cmd = args[0] if args else ""
        if cmd == "claude":
            return claude_proc
        if len(args) > 1 and args[1] == "status":
            return git_status
        return _make_process(returncode=0)

    with patch("cagent.agent._resolve_claude", return_value="claude"), \
         patch("cagent.agent.asyncio.create_subprocess_exec", side_effect=mock_exec), \
         patch("cagent.agent.prepare_sandbox"):
        await run_agent(
            task=sample_task, worktree_path=tmp_worktree,
            run_dir=tmp_run_dir, max_turns=10,
        )

    claude_cmd = [c for c in captured_cmds if c[0] == "claude"][0]
    assert "--max-turns" in claude_cmd
    assert "10" in claude_cmd


@pytest.mark.asyncio
async def test_run_agent_no_max_turns_by_default(
    sample_task: Task, tmp_worktree: Path, tmp_run_dir: Path,
) -> None:
    """Without max_turns, --max-turns is NOT in the command."""
    captured_cmds: list[list[str]] = []

    claude_proc = _make_process(returncode=0, stdout_lines=[])
    git_status = _make_process(returncode=0, stdout=b"")

    async def mock_exec(*args, **kwargs):
        captured_cmds.append(list(args))
        cmd = args[0] if args else ""
        if cmd == "claude":
            return claude_proc
        if len(args) > 1 and args[1] == "status":
            return git_status
        return _make_process(returncode=0)

    with patch("cagent.agent._resolve_claude", return_value="claude"), \
         patch("cagent.agent.asyncio.create_subprocess_exec", side_effect=mock_exec), \
         patch("cagent.agent.prepare_sandbox"):
        await run_agent(
            task=sample_task, worktree_path=tmp_worktree,
            run_dir=tmp_run_dir,
        )

    claude_cmd = [c for c in captured_cmds if c[0] == "claude"][0]
    assert "--max-turns" not in claude_cmd


@pytest.mark.asyncio
async def test_run_git_async_success(tmp_worktree: Path) -> None:
    """_run_git_async returns (rc, stdout, stderr) on success."""
    async def mock_exec(*args, **kwargs):
        proc = AsyncMock()
        proc.returncode = 0
        async def mock_communicate():
            return b"HEAD commit", b""
        proc.communicate = mock_communicate
        return proc

    with patch("cagent.git_utils.asyncio.create_subprocess_exec", side_effect=mock_exec):
        rc, stdout, stderr = await _run_git_async("rev-parse", "HEAD", cwd=tmp_worktree)

    assert rc == 0
    assert stdout == "HEAD commit"
    assert stderr == ""


@pytest.mark.asyncio
async def test_run_git_async_timeout(tmp_worktree: Path) -> None:
    """_run_git_async raises RuntimeError on timeout."""
    async def mock_exec(*args, **kwargs):
        proc = AsyncMock()
        proc.returncode = 0
        proc.kill = MagicMock()
        async def hang():
            await asyncio.sleep(999)
            return b"", b""
        proc.communicate = hang
        return proc

    with patch("cagent.git_utils.asyncio.create_subprocess_exec", side_effect=mock_exec):
        with pytest.raises(GitTimeoutError, match="timed out"):
            await _run_git_async("rev-parse", "HEAD", cwd=tmp_worktree, timeout=0.1)
