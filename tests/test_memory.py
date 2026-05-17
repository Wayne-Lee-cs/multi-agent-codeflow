"""Unit tests for cagent/memory.py — RunMemory per-run memory manager."""

import pytest
from pathlib import Path

from cagent.memory import RunMemory


class TestWriteRead:
    def test_write_and_read(self, tmp_path):
        mem = RunMemory(tmp_path)
        mem.write("task-001", "created login form")
        assert mem.read("task-001") == "created login form"

    def test_read_nonexistent_returns_empty(self, tmp_path):
        mem = RunMemory(tmp_path)
        assert mem.read("task-999") == ""

    def test_write_overwrites(self, tmp_path):
        mem = RunMemory(tmp_path)
        mem.write("task-001", "v1")
        mem.write("task-001", "v2")
        assert mem.read("task-001") == "v2"


class TestAppend:
    def test_append_creates_file(self, tmp_path):
        mem = RunMemory(tmp_path)
        mem.append("_integrator", "resolved conflict in foo.py")
        assert mem.read("_integrator") == "resolved conflict in foo.py"

    def test_append_preserves_existing(self, tmp_path):
        mem = RunMemory(tmp_path)
        mem.append("_integrator", "first resolution")
        mem.append("_integrator", "second resolution")
        content = mem.read("_integrator")
        assert "first resolution" in content
        assert "second resolution" in content
        assert "---" in content  # separator

    def test_append_no_separator_on_first_write(self, tmp_path):
        mem = RunMemory(tmp_path)
        mem.append("_integrator", "only entry")
        content = mem.read("_integrator")
        assert "---" not in content


class TestReadAll:
    def test_read_all_excludes_shared_context(self, tmp_path):
        mem = RunMemory(tmp_path)
        mem.write("task-001", "task 1 output")
        mem.write("task-002", "task 2 output")
        mem.write_shared("shared context")
        all_mem = mem.read_all()
        assert "task-001" in all_mem
        assert "task-002" in all_mem
        assert "shared_context" not in all_mem

    def test_read_all_empty(self, tmp_path):
        mem = RunMemory(tmp_path)
        assert mem.read_all() == {}


class TestBuildSharedContext:
    def test_basic_build(self, tmp_path):
        mem = RunMemory(tmp_path)
        mem.write("001", "created types.py")
        mem.write("002", "created users.py")
        ctx = mem.build_shared_context(["001", "002"])
        assert "Task 001" in ctx
        assert "created types.py" in ctx
        assert "Task 002" in ctx

    def test_max_chars_cap(self, tmp_path):
        mem = RunMemory(tmp_path)
        mem.write("001", "x" * 3000)
        mem.write("002", "y" * 3000)
        ctx = mem.build_shared_context(["001", "002"], max_chars=4000)
        # Should include first but not second (exceeds cap)
        assert "Task 001" in ctx
        assert "Task 002" not in ctx

    def test_cache_returns_same_result(self, tmp_path):
        mem = RunMemory(tmp_path)
        mem.write("001", "output")
        ctx1 = mem.build_shared_context(["001"])
        ctx2 = mem.build_shared_context(["001"])
        assert ctx1 == ctx2

    def test_cache_invalidates_on_new_ids(self, tmp_path):
        mem = RunMemory(tmp_path)
        mem.write("001", "output 1")
        ctx1 = mem.build_shared_context(["001"])
        mem.write("002", "output 2")
        ctx2 = mem.build_shared_context(["001", "002"])
        assert ctx1 != ctx2
        assert "Task 002" in ctx2

    def test_skips_nonexistent_ids(self, tmp_path):
        mem = RunMemory(tmp_path)
        mem.write("001", "output")
        ctx = mem.build_shared_context(["001", "999"])
        assert "Task 001" in ctx
        assert "Task 999" not in ctx


class TestWriteShared:
    def test_write_and_load_shared(self, tmp_path):
        mem = RunMemory(tmp_path)
        mem.write_shared("global conventions content")
        assert mem.load_shared() == "global conventions content"

    def test_load_shared_nonexistent(self, tmp_path):
        mem = RunMemory(tmp_path)
        assert mem.load_shared() == ""


class TestFileIsolation:
    def test_memory_files_in_run_dir(self, tmp_path):
        mem = RunMemory(tmp_path)
        mem.write("task-001", "content")
        # File should be inside run_dir/memory/
        mem_file = tmp_path / "memory" / "task-001.md"
        assert mem_file.exists()
        assert mem_file.read_text(encoding="utf-8") == "content"

    def test_different_run_dirs_isolated(self, tmp_path):
        run1 = tmp_path / "run1"
        run2 = tmp_path / "run2"
        mem1 = RunMemory(run1)
        mem2 = RunMemory(run2)
        mem1.write("task-001", "run 1 output")
        mem2.write("task-001", "run 2 output")
        assert mem1.read("task-001") == "run 1 output"
        assert mem2.read("task-001") == "run 2 output"
