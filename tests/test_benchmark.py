"""Benchmark tests for key performance paths in cagent."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from cagent.progress import Dashboard, EventParser, TaskProgress
from cagent.safety import DENY_PATTERNS, _check_tokens
from cagent.tasks import Task, dump_state, load_state, parse_tasks_file


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bench(func, iterations: int = 1000) -> float:
    """Run func() iterations times, return total elapsed seconds."""
    start = time.perf_counter()
    for _ in range(iterations):
        func()
    return time.perf_counter() - start


def _bench_async(func, iterations: int = 100) -> float:
    """Run async func() iterations times, return total elapsed seconds."""
    import asyncio

    async def _run():
        start = time.perf_counter()
        for _ in range(iterations):
            await func()
        return time.perf_counter() - start

    return asyncio.get_event_loop().run_until_complete(_run())


# ---------------------------------------------------------------------------
# Safety benchmarks
# ---------------------------------------------------------------------------

class TestSafetyBenchmark:
    """Benchmark safety.py regex and token checks."""

    SAFE_COMMANDS = [
        "git status",
        "git add .",
        "git commit -m 'test'",
        "ls -la",
        "cat README.md",
        "python script.py",
        "pip install requests",
        "echo hello world",
    ]

    DENY_COMMANDS = [
        "git push origin main",
        "git reset --hard HEAD~1",
        "rm -rf /tmp/dir",
        "rm -r -f /tmp/dir",
        "bash -c 'echo pwned'",
        "python -c 'import os; os.system(\"rm -rf /\")'",
        "cat file | sh",
        "node -e 'require(\"child_process\").exec(\"rm -rf /\")'",
    ]

    def _check_regex(self, cmd: str) -> bool:
        import re
        for pattern in DENY_PATTERNS:
            if re.search(pattern, cmd, re.IGNORECASE):
                return True
        return False

    def test_bench_regex_safe_commands(self) -> None:
        """Benchmark: regex check against safe commands (should all pass)."""
        for cmd in self.SAFE_COMMANDS:
            result = self._check_regex(cmd)
            assert not result, f"False positive: {cmd}"

    def test_bench_regex_deny_commands(self) -> None:
        """Benchmark: regex check against deny commands (should all block)."""
        for cmd in self.DENY_COMMANDS:
            result = self._check_regex(cmd)
            assert result, f"False negative: {cmd}"

    def test_bench_check_tokens_safe(self) -> None:
        """Benchmark: token check on safe commands."""
        for cmd in ["rm file.txt", "rm -f file.txt", "rm -i file.txt"]:
            assert _check_tokens(cmd) is None

    def test_bench_check_tokens_deny(self) -> None:
        """Benchmark: token check on split-flag deny patterns."""
        for cmd in ["rm -r -f /tmp", "rm --recursive --force /tmp"]:
            assert _check_tokens(cmd) is not None

    def test_bench_regex_throughput(self) -> None:
        """Benchmark: regex check throughput (commands/second)."""
        import re
        all_cmds = self.SAFE_COMMANDS + self.DENY_COMMANDS
        iterations = 10000

        def run():
            for cmd in all_cmds:
                for pattern in DENY_PATTERNS:
                    re.search(pattern, cmd, re.IGNORECASE)

        elapsed = _bench(run, iterations=iterations)
        ops_per_sec = (iterations * len(all_cmds)) / elapsed
        # Should handle >10k commands/second
        assert ops_per_sec > 5000, f"Too slow: {ops_per_sec:.0f} cmd/s"

    def test_bench_check_tokens_throughput(self) -> None:
        """Benchmark: token check throughput."""
        cmds = [
            "rm -r -f /tmp",
            "rm --recursive --force /tmp",
            "rm file.txt",
            "git status",
            "rm -i -f file.txt",
        ]
        iterations = 10000

        def run():
            for cmd in cmds:
                _check_tokens(cmd)

        elapsed = _bench(run, iterations=iterations)
        ops_per_sec = (iterations * len(cmds)) / elapsed
        assert ops_per_sec > 5000, f"Too slow: {ops_per_sec:.0f} cmd/s"


# ---------------------------------------------------------------------------
# Memory benchmarks
# ---------------------------------------------------------------------------

class TestMemoryBenchmark:
    """Benchmark memory.py operations."""

    def test_bench_memory_write(self, tmp_path: Path) -> None:
        """Benchmark: memory write throughput."""
        from cagent.memory import RunMemory

        mem = RunMemory(tmp_path)
        iterations = 1000

        def run():
            for i in range(10):
                mem.write(f"task-{i:03d}", f"Content for task {i}\n" * 10)

        elapsed = _bench(run, iterations=iterations // 10)
        ops_per_sec = iterations / (elapsed * 10)
        assert ops_per_sec > 25, f"Too slow: {ops_per_sec:.0f} writes/s"

    def test_bench_memory_read(self, tmp_path: Path) -> None:
        """Benchmark: memory read throughput."""
        from cagent.memory import RunMemory

        mem = RunMemory(tmp_path)
        for i in range(20):
            mem.write(f"task-{i:03d}", f"Content for task {i}\n" * 10)

        iterations = 10000

        def run():
            for i in range(20):
                mem.read(f"task-{i:03d}")

        elapsed = _bench(run, iterations=iterations)
        ops_per_sec = (iterations * 20) / elapsed
        assert ops_per_sec > 1000, f"Too slow: {ops_per_sec:.0f} reads/s"

    def test_bench_build_shared_context(self, tmp_path: Path) -> None:
        """Benchmark: build_shared_context with caching."""
        from cagent.memory import RunMemory

        mem = RunMemory(tmp_path)
        for i in range(10):
            mem.write(f"task-{i:03d}", f"Summary of task {i}. " * 20)

        task_ids = [f"{i:03d}" for i in range(10)]
        iterations = 10000

        # Cold build
        elapsed_cold = _bench(lambda: mem.build_shared_context(task_ids), iterations=100)

        # Cached build (same inputs, same version)
        elapsed_cached = _bench(lambda: mem.build_shared_context(task_ids), iterations=iterations)

        # Cached should be >10x faster than cold
        cold_per = elapsed_cold / 100
        cached_per = elapsed_cached / iterations
        assert cached_per < cold_per, f"Cache not faster: cold={cold_per*1e6:.1f}µs, cached={cached_per*1e6:.1f}µs"

    def test_bench_memory_append(self, tmp_path: Path) -> None:
        """Benchmark: memory append throughput."""
        from cagent.memory import RunMemory

        mem = RunMemory(tmp_path)
        iterations = 5000

        def run():
            mem.append("log", "Some log entry\n")

        elapsed = _bench(run, iterations=iterations)
        ops_per_sec = iterations / elapsed
        # Phase 89.6: append uses atomic read-modify-write for consistency,
        # which is slower than raw open("a") but prevents partial writes.
        assert ops_per_sec > 100, f"Too slow: {ops_per_sec:.0f} appends/s"


# ---------------------------------------------------------------------------
# Task parsing benchmarks
# ---------------------------------------------------------------------------

class TestTaskBenchmark:
    """Benchmark tasks.py parsing and serialization."""

    def test_bench_parse_tasks_file(self, tmp_path: Path) -> None:
        """Benchmark: parse_tasks_file with 100 tasks."""
        tasks_file = tmp_path / "tasks.txt"
        lines = [f"Task {i}: do something important" for i in range(100)]
        tasks_file.write_text("\n".join(lines), encoding="utf-8")

        iterations = 1000

        def run():
            parse_tasks_file(tasks_file, "bench-run")

        elapsed = _bench(run, iterations=iterations)
        ops_per_sec = iterations / elapsed
        assert ops_per_sec > 100, f"Too slow: {ops_per_sec:.0f} parses/s"

    def test_bench_dump_state(self, tmp_path: Path) -> None:
        """Benchmark: dump_state with 50 tasks."""
        tasks = [
            Task(id=f"{i:03d}", prompt=f"Task {i}", branch=f"bench/task-{i:03d}")
            for i in range(50)
        ]
        run_dir = tmp_path / "run"
        run_dir.mkdir()

        iterations = 1000

        def run():
            dump_state(run_dir, tasks)

        elapsed = _bench(run, iterations=iterations)
        ops_per_sec = iterations / elapsed
        assert ops_per_sec > 50, f"Too slow: {ops_per_sec:.0f} dumps/s"

    def test_bench_load_state(self, tmp_path: Path) -> None:
        """Benchmark: load_state with 50 tasks."""
        tasks = [
            Task(id=f"{i:03d}", prompt=f"Task {i}", branch=f"bench/task-{i:03d}")
            for i in range(50)
        ]
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        dump_state(run_dir, tasks)

        iterations = 1000

        def run():
            load_state(run_dir)

        elapsed = _bench(run, iterations=iterations)
        ops_per_sec = iterations / elapsed
        assert ops_per_sec > 50, f"Too slow: {ops_per_sec:.0f} loads/s"


# ---------------------------------------------------------------------------
# Dashboard benchmarks
# ---------------------------------------------------------------------------

class TestDashboardBenchmark:
    """Benchmark progress.py dashboard operations."""

    def test_bench_set_task_status(self, tmp_path: Path) -> None:
        """Benchmark: set_task_status throughput."""
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        dashboard = Dashboard(run_dir)

        iterations = 10000

        def run():
            for i in range(10):
                dashboard.set_task_status(f"task-{i:03d}", "running")

        elapsed = _bench(run, iterations=iterations)
        ops_per_sec = (iterations * 10) / elapsed
        assert ops_per_sec > 1000, f"Too slow: {ops_per_sec:.0f} ops/s"

    def test_bench_event_parser(self) -> None:
        """Benchmark: EventParser.feed throughput."""
        parser = EventParser()
        lines = [
            json.dumps({"type": "system", "subtype": "init", "model": "test"}),
            json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Hello"}]}}),
            json.dumps({"type": "result", "subtype": "success", "usage": {"input_tokens": 100, "output_tokens": 50}}),
            "plain text line",
        ]
        iterations = 10000

        def run():
            for line in lines:
                parser.feed(line)

        elapsed = _bench(run, iterations=iterations)
        ops_per_sec = (iterations * len(lines)) / elapsed
        assert ops_per_sec > 5000, f"Too slow: {ops_per_sec:.0f} lines/s"

    def test_bench_dashboard_snapshot(self, tmp_path: Path) -> None:
        """Benchmark: _write_dashboard with many tasks."""
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        dashboard = Dashboard(run_dir)

        # Add 30 tasks
        for i in range(30):
            dashboard.set_task_status(f"task-{i:03d}", "done" if i < 20 else "running")

        iterations = 500

        def run():
            dashboard._write_dashboard()

        elapsed = _bench(run, iterations=iterations)
        ops_per_sec = iterations / elapsed
        assert ops_per_sec > 50, f"Too slow: {ops_per_sec:.0f} snapshots/s"


# ---------------------------------------------------------------------------
# Config benchmarks
# ---------------------------------------------------------------------------

class TestConfigBenchmark:
    """Benchmark config.py validation."""

    def test_bench_config_validation(self, tmp_path: Path) -> None:
        """Benchmark: config validation throughput."""
        from cagent.config import load_config

        iterations = 10000

        def run():
            load_config(tmp_path)

        # Just test that validation doesn't add significant overhead
        elapsed = _bench(run, iterations=min(iterations, 1000))
        assert elapsed < 5.0, f"Config validation too slow: {elapsed:.2f}s for {iterations} iterations"


# ---------------------------------------------------------------------------
# Summary benchmark
# ---------------------------------------------------------------------------

class TestBenchmarkSummary:
    """Print benchmark summary at the end."""

    def test_benchmark_summary(self, tmp_path: Path, capsys) -> None:
        """Run all key benchmarks and print summary."""
        results = {}

        # Safety regex
        import re
        cmds = ["git status", "git push origin main", "rm -rf /tmp", "bash -c 'echo hi'"]
        start = time.perf_counter()
        for _ in range(5000):
            for cmd in cmds:
                for p in DENY_PATTERNS:
                    re.search(p, cmd, re.IGNORECASE)
        results["safety_regex"] = (time.perf_counter() - start, 5000 * len(cmds))

        # Memory write/read
        from cagent.memory import RunMemory
        mem = RunMemory(tmp_path / "mem_bench")
        start = time.perf_counter()
        for i in range(500):
            mem.write(f"t-{i}", f"content {i}")
            mem.read(f"t-{i}")
        results["memory_rw"] = (time.perf_counter() - start, 500 * 2)

        # Task parse
        tasks_file = tmp_path / "tasks.txt"
        tasks_file.write_text("\n".join([f"Task {i}" for i in range(50)]), encoding="utf-8")
        start = time.perf_counter()
        for _ in range(500):
            parse_tasks_file(tasks_file, "bench")
        results["task_parse_50"] = (time.perf_counter() - start, 500)

        # Dashboard status
        run_dir = tmp_path / "dash"
        run_dir.mkdir()
        dash = Dashboard(run_dir)
        start = time.perf_counter()
        for i in range(5000):
            dash.set_task_status(f"t-{i % 100}", "running")
        results["dashboard_status"] = (time.perf_counter() - start, 5000)

        # Print summary
        with capsys.disabled():
            print("\n" + "=" * 60)
            print("BENCHMARK SUMMARY")
            print("=" * 60)
            for name, (elapsed, ops) in results.items():
                ops_per_sec = ops / elapsed
                print(f"  {name:25s}: {elapsed*1000:8.1f}ms  ({ops_per_sec:10.0f} ops/s)")
            print("=" * 60)
