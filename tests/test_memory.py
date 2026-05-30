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
        # Both tasks included but truncated to fit within cap
        assert "Task 001" in ctx
        assert "Task 002" in ctx
        assert len(ctx) <= 4000

    def test_max_chars_preserves_all_tasks(self, tmp_path):
        """All tasks appear even when total exceeds max_chars (truncated, not dropped)."""
        mem = RunMemory(tmp_path)
        mem.write("001", "a" * 2000)
        mem.write("002", "b" * 2000)
        mem.write("003", "c" * 2000)
        ctx = mem.build_shared_context(["001", "002", "003"], max_chars=3000)
        assert "Task 001" in ctx
        assert "Task 002" in ctx
        assert "Task 003" in ctx
        assert len(ctx) <= 3000

    def test_empty_tasks_returns_empty(self, tmp_path):
        """No task memories returns empty string."""
        mem = RunMemory(tmp_path)
        ctx = mem.build_shared_context(["001", "002"], max_chars=4000)
        assert ctx == ""

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

    def test_cache_invalidates_on_overwrite(self, tmp_path):
        """Version counter invalidates cache when same file is overwritten."""
        mem = RunMemory(tmp_path)
        mem.write("001", "v1 content")
        ctx1 = mem.build_shared_context(["001"])
        assert "v1 content" in ctx1
        mem.write("001", "v2 content")
        ctx2 = mem.build_shared_context(["001"])
        assert "v2 content" in ctx2
        assert ctx1 != ctx2

    def test_cache_invalidates_on_append(self, tmp_path):
        """Version counter invalidates cache on append."""
        mem = RunMemory(tmp_path)
        mem.write("001", "initial")
        ctx1 = mem.build_shared_context(["001"])
        mem.append("001", "appended")
        ctx2 = mem.build_shared_context(["001"])
        assert "appended" in ctx2
        assert ctx1 != ctx2

    def test_version_counter_no_stat_calls(self, tmp_path):
        """Cache key uses version counter, not file stat (no _get_mtime)."""
        mem = RunMemory(tmp_path)
        mem.write("001", "output")
        # build_shared_context should not call _get_mtime
        ctx = mem.build_shared_context(["001"])
        assert "output" in ctx
        # Verify _get_mtime method no longer exists
        assert not hasattr(mem, "_get_mtime")

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


class TestAtomicWrite:
    """Tests for memory write atomicity."""

    def test_write_uses_atomic_write(self, tmp_path):
        """write() should use atomic_write to avoid partial reads from concurrent threads."""
        mem = RunMemory(tmp_path)
        mem.write("task-001", "atomic content")
        mem_file = tmp_path / "memory" / "task-001.md"
        assert mem_file.exists()
        assert mem_file.read_text(encoding="utf-8") == "atomic content"

    def test_write_no_leftover_tmp_files(self, tmp_path):
        """atomic_write should clean up .tmp files after success."""
        mem = RunMemory(tmp_path)
        mem.write("task-001", "content")
        mem_dir = tmp_path / "memory"
        tmp_files = list(mem_dir.glob("*.tmp"))
        assert len(tmp_files) == 0


class TestAgentIdValidation:
    """Tests for _validate_agent_id path traversal prevention (Phase 66.3)."""

    def test_dotdot_slash_raises_value_error(self, tmp_path):
        mem = RunMemory(tmp_path)
        with pytest.raises(ValueError, match="Invalid agent_id"):
            mem.write("../../../etc/passwd", "evil")

    def test_slash_raises_value_error(self, tmp_path):
        mem = RunMemory(tmp_path)
        with pytest.raises(ValueError, match="Invalid agent_id"):
            mem.write("sub/dir", "evil")

    def test_backslash_raises_value_error(self, tmp_path):
        mem = RunMemory(tmp_path)
        with pytest.raises(ValueError, match="Invalid agent_id"):
            mem.write("sub\\dir", "evil")

    def test_empty_string_raises_value_error(self, tmp_path):
        mem = RunMemory(tmp_path)
        with pytest.raises(ValueError, match="Invalid agent_id"):
            mem.write("", "content")

    def test_normal_id_passes(self, tmp_path):
        mem = RunMemory(tmp_path)
        mem.write("task-001", "safe content")
        assert mem.read("task-001") == "safe content"

    def test_integrator_literal_passes(self, tmp_path):
        mem = RunMemory(tmp_path)
        mem.write("_integrator", "integrator output")
        assert mem.read("_integrator") == "integrator output"

    def test_append_rejects_traversal(self, tmp_path):
        mem = RunMemory(tmp_path)
        with pytest.raises(ValueError, match="Invalid agent_id"):
            mem.append("../evil", "content")

    def test_read_rejects_traversal(self, tmp_path):
        mem = RunMemory(tmp_path)
        with pytest.raises(ValueError, match="Invalid agent_id"):
            mem.read("../evil")


class TestAppendAtomic:
    """Phase 89.6: memory.append uses atomic read-modify-write."""

    def test_append_creates_file_on_first_call(self, tmp_path):
        """First append to non-existent file creates it."""
        mem = RunMemory(tmp_path)
        mem.append("task-1", "first entry")
        content = mem.read("task-1")
        assert content == "first entry"

    def test_append_preserves_existing_content(self, tmp_path):
        """Subsequent appends preserve previous content with separator."""
        mem = RunMemory(tmp_path)
        mem.append("task-1", "first entry")
        mem.append("task-1", "second entry")
        content = mem.read("task-1")
        assert "first entry" in content
        assert "second entry" in content
        assert "---" in content  # separator

    def test_append_no_separator_on_first_write(self, tmp_path):
        """First write has no separator prefix."""
        mem = RunMemory(tmp_path)
        mem.append("task-1", "only entry")
        content = mem.read("task-1")
        assert not content.startswith("---")
        assert content == "only entry"

    def test_append_is_atomic_no_tmp_residual(self, tmp_path):
        """Atomic write leaves no .tmp files behind."""
        mem = RunMemory(tmp_path)
        mem.append("task-1", "entry")
        tmp_files = list((tmp_path / "memory").glob("*.tmp"))
        assert tmp_files == []
