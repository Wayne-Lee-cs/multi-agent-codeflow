"""Unit tests for cagent/tasks.py — task parsing, serialization, validation."""

import json
from pathlib import Path

import pytest

from cagent.tasks import Task, dump_state, load_state, parse_tasks_file


class TestParseTasksFile:
    def test_basic_parsing(self, tmp_path):
        f = tmp_path / "tasks.txt"
        f.write_text("Add login form\nCreate JWT middleware\n", encoding="utf-8")
        tasks = parse_tasks_file(f, "run-001")
        assert len(tasks) == 2
        assert tasks[0].id == "001"
        assert tasks[0].prompt == "Add login form"
        assert tasks[0].branch == "cagent/run-001/task-001"
        assert tasks[0].status == "pending"
        assert tasks[1].id == "002"
        assert tasks[1].prompt == "Create JWT middleware"

    def test_skip_empty_and_comments(self, tmp_path):
        f = tmp_path / "tasks.txt"
        f.write_text("# comment\n\nTask A\n# another\n\nTask B\n", encoding="utf-8")
        tasks = parse_tasks_file(f, "run-001")
        assert len(tasks) == 2
        assert tasks[0].prompt == "Task A"
        assert tasks[1].prompt == "Task B"

    def test_unicode_and_emoji(self, tmp_path):
        f = tmp_path / "tasks.txt"
        f.write_text("添加登录表单\nCreate JWT 🔑\n", encoding="utf-8")
        tasks = parse_tasks_file(f, "run-001")
        assert len(tasks) == 2
        assert tasks[0].prompt == "添加登录表单"
        assert "🔑" in tasks[1].prompt

    def test_empty_file_raises(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_text("", encoding="utf-8")
        with pytest.raises(ValueError, match="No tasks found"):
            parse_tasks_file(f, "run-001")

    def test_comments_only_raises(self, tmp_path):
        f = tmp_path / "comments.txt"
        f.write_text("# only comments\n# here\n", encoding="utf-8")
        with pytest.raises(ValueError, match="No tasks found"):
            parse_tasks_file(f, "run-001")

    def test_missing_file_raises(self, tmp_path):
        f = tmp_path / "nonexistent.txt"
        with pytest.raises(FileNotFoundError, match="Tasks file not found"):
            parse_tasks_file(f, "run-001")

    def test_invalid_encoding_raises(self, tmp_path):
        f = tmp_path / "bad.txt"
        f.write_bytes(b"\x80\x81\x82")
        with pytest.raises(ValueError, match="not valid UTF-8"):
            parse_tasks_file(f, "run-001")

    def test_long_line(self, tmp_path):
        long_prompt = "A" * 10000
        f = tmp_path / "long.txt"
        f.write_text(long_prompt, encoding="utf-8")
        tasks = parse_tasks_file(f, "run-001")
        assert len(tasks) == 1
        assert len(tasks[0].prompt) == 10000


class TestDumpLoadState:
    def test_round_trip(self, tmp_path):
        tasks = [
            Task(id="001", prompt="task A", branch="cagent/run/task-001", status="done", commit_sha="abc123"),
            Task(id="002", prompt="task B", branch="cagent/run/task-002", status="failed"),
        ]
        dump_state(tmp_path, tasks)
        loaded = load_state(tmp_path)
        assert len(loaded) == 2
        assert loaded[0].id == "001"
        assert loaded[0].status == "done"
        assert loaded[0].commit_sha == "abc123"
        assert loaded[1].id == "002"
        assert loaded[1].status == "failed"
        assert loaded[1].commit_sha is None

    def test_invalid_status_raises(self, tmp_path):
        """load_state should reject invalid status values."""
        target = tmp_path / "tasks.json"
        data = [{"id": "001", "prompt": "x", "branch": "b", "status": "INVALID"}]
        target.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(ValueError, match="Invalid status"):
            load_state(tmp_path)

    def test_empty_branch_raises(self, tmp_path):
        """load_state should reject empty branch."""
        target = tmp_path / "tasks.json"
        data = [{"id": "001", "prompt": "x", "branch": "", "status": "pending"}]
        target.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(ValueError, match="Missing branch"):
            load_state(tmp_path)

    def test_missing_tasks_json_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="No tasks.json"):
            load_state(tmp_path)

    def test_extra_keys_ignored(self, tmp_path):
        """load_state should ignore unknown keys (forward-compat)."""
        target = tmp_path / "tasks.json"
        data = [{"id": "001", "prompt": "x", "branch": "b", "status": "pending", "future_field": 42, "another": "abc"}]
        target.write_text(json.dumps(data), encoding="utf-8")
        tasks = load_state(tmp_path)
        assert len(tasks) == 1
        assert tasks[0].id == "001"

    def test_missing_log_path_defaults(self, tmp_path):
        """load_state should handle missing log_path gracefully."""
        import os
        target = tmp_path / "tasks.json"
        data = [{"id": "001", "prompt": "x", "branch": "b", "status": "pending"}]
        target.write_text(json.dumps(data), encoding="utf-8")
        tasks = load_state(tmp_path)
        assert len(tasks) == 1
        assert tasks[0].log_path == Path(os.devnull)
