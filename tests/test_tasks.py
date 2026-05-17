"""Unit tests for cagent/tasks.py — task parsing, serialization, validation."""

import json
from pathlib import Path

import pytest

from cagent.tasks import Task, dump_state, load_state, parse_tasks_file, parse_tasks_md


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


class TestParseTasksMd:
    def test_basic_parsing(self, tmp_path):
        f = tmp_path / "tasks.md"
        f.write_text(
            "# Task Plan\n\n"
            "### Task 001\n"
            "- **depends_on**: none\n"
            "- **files**: src/a.py\n\n"
            "Create src/a.py with function foo().\n\n"
            "### Task 002\n"
            "- **depends_on**: 001\n"
            "- **files**: src/b.py\n\n"
            "Create src/b.py importing from src/a.py.\n",
            encoding="utf-8",
        )
        tasks, conv = parse_tasks_md(f, "run-001")
        assert len(tasks) == 2
        assert tasks[0].id == "001"
        assert tasks[0].depends_on == []
        assert "foo()" in tasks[0].prompt
        assert tasks[1].id == "002"
        assert tasks[1].depends_on == ["001"]
        assert conv == ""

    def test_conventions_from_file(self, tmp_path):
        f = tmp_path / "tasks.md"
        f.write_text(
            "# Task Plan\n\n"
            "### Task 001\n"
            "- **depends_on**: none\n\n"
            "Do something.\n",
            encoding="utf-8",
        )
        conv_file = tmp_path / "conventions.md"
        conv_file.write_text(
            "# Global Conventions\n\n- Python 3.11+\n- Type hints\n",
            encoding="utf-8",
        )
        tasks, conv = parse_tasks_md(f, "run-001")
        assert len(tasks) == 1
        assert "Python 3.11+" in conv

    def test_inline_conventions(self, tmp_path):
        f = tmp_path / "tasks.md"
        f.write_text(
            "# Task Plan\n\n"
            "## Conventions\n"
            "- Use snake_case\n"
            "- Google-style docstrings\n\n"
            "### Task 001\n"
            "- **depends_on**: none\n\n"
            "Do something.\n",
            encoding="utf-8",
        )
        tasks, conv = parse_tasks_md(f, "run-001")
        assert "snake_case" in conv

    def test_multiple_depends_on(self, tmp_path):
        f = tmp_path / "tasks.md"
        f.write_text(
            "### Task 003\n"
            "- **depends_on**: 001, 002\n\n"
            "Merge results from 001 and 002.\n",
            encoding="utf-8",
        )
        tasks, _ = parse_tasks_md(f, "run-001")
        assert tasks[0].depends_on == ["001", "002"]

    def test_no_tasks_raises(self, tmp_path):
        f = tmp_path / "empty.md"
        f.write_text("# Just a heading\n\nNo tasks here.\n", encoding="utf-8")
        with pytest.raises(ValueError, match="No tasks found"):
            parse_tasks_md(f, "run-001")

    def test_missing_file_raises(self, tmp_path):
        f = tmp_path / "nonexistent.md"
        with pytest.raises(FileNotFoundError):
            parse_tasks_md(f, "run-001")

    def test_case_insensitive_section_extraction(self, tmp_path):
        """_extract_section should handle mixed-case headings."""
        f = tmp_path / "tasks.md"
        f.write_text(
            "# Task Plan\n\n"
            "## CONVENTIONS\n"
            "- Use snake_case\n\n"
            "## TASKS\n\n"
            "### Task 001\n"
            "- **depends_on**: none\n\n"
            "Do something.\n",
            encoding="utf-8",
        )
        tasks, conv = parse_tasks_md(f, "run-001")
        assert len(tasks) == 1
        assert "snake_case" in conv

    def test_uppercase_conventions_section(self, tmp_path):
        """Uppercase ## CONVENTIONS heading should be extracted."""
        f = tmp_path / "tasks.md"
        f.write_text(
            "# Plan\n\n"
            "## CONVENTIONS\n"
            "- Python 3.11+\n\n"
            "## DETAILS\n\n"
            "### Task 001\n"
            "- **depends_on**: none\n\n"
            "Create module.\n",
            encoding="utf-8",
        )
        tasks, conv = parse_tasks_md(f, "run-001")
        assert "Python 3.11+" in conv
        assert len(tasks) == 1
