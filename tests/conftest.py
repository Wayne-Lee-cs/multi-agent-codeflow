"""Shared fixtures for cagent tests."""

import json
import subprocess
from pathlib import Path

import pytest

from cagent.agent import _resolve_claude


@pytest.fixture(autouse=True)
def _clear_resolve_claude_cache():
    """Clear lru_cache on _resolve_claude between tests to ensure isolation."""
    _resolve_claude.cache_clear()
    yield
    _resolve_claude.cache_clear()


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
    # Create an initial commit so HEAD exists
    readme = tmp_path / "README.md"
    readme.write_text("# test\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=tmp_path, capture_output=True, check=True,
    )
    return tmp_path


@pytest.fixture
def sample_stream_json_events():
    """Sample stream-json events from claude -p --output-format stream-json."""
    return [
        # system.init
        json.dumps({"type": "system", "subtype": "init", "model": "claude-opus-4-7"}),
        # assistant with tool_use
        json.dumps({
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "id": "tu_1", "name": "Edit", "input": {"file_path": "src/foo.py", "old_string": "a", "new_string": "b"}}
                ]
            }
        }),
        # user tool_result (success)
        json.dumps({
            "type": "user",
            "message": {
                "content": [
                    {"type": "tool_result", "tool_use_id": "tu_1", "content": "ok"}
                ]
            }
        }),
        # assistant with text
        json.dumps({
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Done editing the file."}
                ]
            }
        }),
        # result success
        json.dumps({"type": "result", "subtype": "success", "usage": {"input_tokens": 100, "output_tokens": 50}}),
    ]
