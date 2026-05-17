"""Benchmark: cagent (concurrent) vs single-agent (sequential) execution.

Runs the same tasks through both paths and compares wall-clock time.
"""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


def _find_claude() -> str:
    """Find claude CLI, handling Windows .cmd extension."""
    import shutil
    for name in ("claude", "claude.cmd", "claude.exe"):
        path = shutil.which(name)
        if path:
            return path
    return "claude"


def run_single_agent(tasks_file: Path, timeout: int = 300) -> dict:
    """Run tasks sequentially using a single claude -p instance."""
    tasks = [
        line.strip()
        for line in tasks_file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    claude_bin = _find_claude()
    results = []
    total_start = time.monotonic()

    for i, prompt in enumerate(tasks):
        task_start = time.monotonic()
        try:
            proc = subprocess.run(
                [claude_bin, "-p", prompt, "--permission-mode", "acceptEdits",
                 "--output-format", "text"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                cwd=str(Path.cwd()),
            )
            elapsed = time.monotonic() - task_start
            results.append({
                "task": i + 1,
                "status": "done" if proc.returncode == 0 else "failed",
                "elapsed": round(elapsed, 1),
                "returncode": proc.returncode,
            })
        except subprocess.TimeoutExpired:
            elapsed = time.monotonic() - task_start
            results.append({
                "task": i + 1,
                "status": "timeout",
                "elapsed": round(elapsed, 1),
            })
        except Exception as e:
            elapsed = time.monotonic() - task_start
            results.append({
                "task": i + 1,
                "status": "error",
                "elapsed": round(elapsed, 1),
                "error": str(e),
            })

    total_elapsed = time.monotonic() - total_start
    return {
        "mode": "single-agent",
        "tasks": len(tasks),
        "total_elapsed": round(total_elapsed, 1),
        "results": results,
    }


def run_cagent(tasks_file: Path, concurrency: int = 4, timeout: int = 300) -> dict:
    """Run tasks concurrently using cagent."""
    total_start = time.monotonic()

    try:
        proc = subprocess.run(
            [sys.executable, "-m", "cagent", "run", str(tasks_file),
             "-j", str(concurrency), "--timeout", str(timeout)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout * 2,  # cagent total timeout is longer
            cwd=str(Path.cwd()),
        )
        total_elapsed = time.monotonic() - total_start
        return {
            "mode": f"cagent (j={concurrency})",
            "total_elapsed": round(total_elapsed, 1),
            "returncode": proc.returncode,
            "stdout": proc.stdout[-500:] if proc.stdout else "",
            "stderr": proc.stderr[-500:] if proc.stderr else "",
        }
    except subprocess.TimeoutExpired:
        total_elapsed = time.monotonic() - total_start
        return {
            "mode": f"cagent (j={concurrency})",
            "total_elapsed": round(total_elapsed, 1),
            "returncode": -1,
            "error": "timeout",
        }
    except Exception as e:
        total_elapsed = time.monotonic() - total_start
        return {
            "mode": f"cagent (j={concurrency})",
            "total_elapsed": round(total_elapsed, 1),
            "returncode": -1,
            "error": str(e),
        }


def main():
    tasks_file = Path("tasks/benchmark.txt")
    if not tasks_file.exists():
        print(f"Error: {tasks_file} not found")
        sys.exit(1)

    tasks = [
        line.strip()
        for line in tasks_file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    print(f"Benchmark: {len(tasks)} tasks")
    print(f"Tasks file: {tasks_file}")
    print()

    # Clean up any previous benchmark artifacts
    for f in ["bench_a.md", "bench_b.md", "bench_c.md", "bench_d.md"]:
        p = Path(f)
        if p.exists():
            p.unlink()

    # Run single agent (sequential)
    print("=" * 60)
    print("MODE 1: Single Agent (sequential)")
    print("=" * 60)
    single = run_single_agent(tasks_file)
    print(f"Total time: {single['total_elapsed']}s")
    for r in single["results"]:
        print(f"  Task {r['task']}: {r['status']} ({r['elapsed']}s)")
    print()

    # Clean up between runs
    for f in ["bench_a.md", "bench_b.md", "bench_c.md", "bench_d.md"]:
        p = Path(f)
        if p.exists():
            p.unlink()

    # Run cagent (concurrent)
    print("=" * 60)
    print("MODE 2: cagent (concurrent, j=4)")
    print("=" * 60)
    concurrent = run_cagent(tasks_file, concurrency=4)
    print(f"Total time: {concurrent['total_elapsed']}s")
    if "stdout" in concurrent:
        print(f"Output:\n{concurrent['stdout']}")
    if concurrent.get("error"):
        print(f"Error: {concurrent['error']}")
    print()

    # Summary
    print("=" * 60)
    print("COMPARISON")
    print("=" * 60)
    s_time = single["total_elapsed"]
    c_time = concurrent["total_elapsed"]
    if c_time > 0:
        speedup = s_time / c_time
        print(f"Single agent: {s_time}s")
        print(f"cagent:       {c_time}s")
        print(f"Speedup:      {speedup:.2f}x")
        if speedup > 1:
            print(f"cagent is {speedup:.1f}x faster")
        else:
            print(f"Single agent is {1/speedup:.1f}x faster (overhead dominates for simple tasks)")
    else:
        print("cagent failed to run")


if __name__ == "__main__":
    main()
