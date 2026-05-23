"""Shared fixtures and helpers for cagent tests."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

import cagent.agent as _agent_module
from cagent.agent import _resolve_claude


class AsyncLineIterator:
    """Async iterator that yields lines from a list."""

    def __init__(self, lines: list[bytes]):
        self._lines = iter(lines)

    def __aiter__(self):
        return self

    async def __anext__(self) -> bytes:
        try:
            return next(self._lines)
        except StopIteration:
            raise StopAsyncIteration


def _make_process(
    returncode: int = 0,
    stdout: bytes = b"",
    stderr: bytes = b"",
    pid: int = 12345,
    stdout_lines: list[bytes] | None = None,
) -> AsyncMock:
    """Create a mock asyncio subprocess."""
    proc = AsyncMock()
    proc.pid = pid
    proc.returncode = returncode
    proc.stdin = AsyncMock()
    proc.stdin.write = MagicMock()
    proc.stdin.drain = AsyncMock()
    proc.stdin.close = MagicMock()
    proc.stdin.wait_closed = AsyncMock()
    proc.stdout = AsyncMock()
    if stdout_lines is not None:
        proc.stdout.__aiter__ = MagicMock(
            return_value=AsyncLineIterator(stdout_lines)
        )
    else:
        proc.stdout.__aiter__ = MagicMock(return_value=AsyncLineIterator([]))

    async def mock_communicate():
        return (stdout, stderr)

    proc.communicate = mock_communicate

    async def mock_wait():
        proc.returncode = returncode
        return returncode

    proc.wait = mock_wait
    proc.terminate = MagicMock()
    proc.kill = MagicMock()
    return proc


@pytest.fixture(autouse=True)
def _clear_resolve_claude_cache():
    """Clear manual cache on _resolve_claude between tests to ensure isolation."""
    _agent_module._claude_path_cache = None
    yield
    _agent_module._claude_path_cache = None


@pytest.fixture
def tmp_repo(tmp_path):
    """Create a temporary git repository for testing."""
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path, capture_output=True, check=True,
    )
    readme = tmp_path / "README.md"
    readme.write_text("# test\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=tmp_path, capture_output=True, check=True,
    )
    return tmp_path


@pytest.fixture
def tmp_worktree(tmp_path: Path) -> Path:
    wt = tmp_path / "worktree"
    wt.mkdir()
    return wt


@pytest.fixture
def tmp_run_dir(tmp_path: Path) -> Path:
    rd = tmp_path / "run"
    rd.mkdir()
    return rd


@pytest.fixture
def sample_task() -> "Task":
    from cagent.tasks import Task
    return Task(id="001", prompt="Test task prompt", branch="task-001")


@pytest.fixture
def sample_stream_json_events():
    """Sample stream-json events from claude -p --output-format stream-json."""
    return [
        json.dumps({"type": "system", "subtype": "init", "model": "claude-opus-4-7"}),
        json.dumps({
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "id": "tu_1", "name": "Edit", "input": {"file_path": "src/foo.py", "old_string": "a", "new_string": "b"}}
                ]
            }
        }),
        json.dumps({
            "type": "user",
            "message": {
                "content": [
                    {"type": "tool_result", "tool_use_id": "tu_1", "content": "ok"}
                ]
            }
        }),
        json.dumps({
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Done editing the file."}
                ]
            }
        }),
        json.dumps({"type": "result", "subtype": "success", "usage": {"input_tokens": 100, "output_tokens": 50}}),
    ]