"""Agent subprocess management — spawn claude -p, stream events, commit results."""

from __future__ import annotations

import asyncio
import collections
import functools
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from cagent.git_utils import GitTimeoutError
from cagent.memory import RunMemory
from cagent.progress import Dashboard, EventParser, _truncate_jsonl_if_large
from cagent.safety import prepare_sandbox
from cagent.tasks import Task

__all__ = ["AgentResult", "run_agent"]

_claude_path_cache: str | None = None


def _resolve_claude() -> str:
    """Find the claude CLI executable, handling Windows .cmd extension."""
    global _claude_path_cache
    if _claude_path_cache is not None:
        return _claude_path_cache
    for name in ("claude", "claude.cmd", "claude.exe"):
        path = shutil.which(name)
        if path:
            _claude_path_cache = path
            return path
    return "claude"  # fallback, will fail at launch with clear error


_CAGENT_GITIGNORE_MARKER = "# cagent worktree exclusions"
_CAGENT_GITIGNORE_LINES = ".claude/\n.env\nnode_modules/\n__pycache__/\n*.pyc\n.venv/\n"


@dataclass
class AgentResult:
    task_id: str
    status: Literal["done", "failed", "noop"]
    commit_sha: str | None = None
    fail_reason: str | None = None
    output_summary: str = ""  # agent's key output text (for memory)
    tokens_in: int = 0
    tokens_out: int = 0


async def run_agent(
    task: Task,
    worktree_path: Path,
    run_dir: Path,
    timeout: int = 1800,
    model_override: str | None = None,
    dashboard: Dashboard | None = None,
    shared_context: str = "",
    memory: RunMemory | None = None,
    conventions: str = "",
    max_turns: int | None = None,
    api_key: str | None = None,
) -> AgentResult:
    """Run a claude -p subprocess for a single task in its worktree.

    Returns an AgentResult with the outcome.
    """
    parser = EventParser()
    output_texts: list[str] = []  # accumulate text events for memory

    # 1. Inject safety sandbox
    prepare_sandbox(worktree_path)

    # 2. Inject standard .gitignore exclusions to prevent accidental commits
    # of sensitive files and build artifacts created by agent tasks.
    # Append rather than overwrite to preserve user-defined rules.
    gitignore_path = worktree_path / ".gitignore"
    existing = gitignore_path.read_text(encoding="utf-8") if gitignore_path.exists() else ""
    if _CAGENT_GITIGNORE_MARKER not in existing:
        prefix = "" if not existing or existing.endswith("\n") else "\n"
        block = f"{prefix}{_CAGENT_GITIGNORE_MARKER}\n{_CAGENT_GITIGNORE_LINES}"
        with open(gitignore_path, "a", encoding="utf-8") as f:
            f.write(block)

    # 3. Build command — always use stdin pipe for prompt delivery.
    # This eliminates command-line length limits (Windows 8191 chars),
    # avoids shell escaping issues, and simplifies the code path.
    parts = []
    if conventions:
        parts.append(f"[Global Conventions]\n{conventions}")
    if shared_context:
        parts.append(f"[Shared context from previous tasks]\n{shared_context}")
    parts.append(f"[Your task]\n{task.prompt}")
    prompt = "\n\n".join(parts)

    claude_bin = _resolve_claude()
    cmd: list[str] = [claude_bin, "-p", "-",
                       "--permission-mode", "acceptEdits",
                       "--output-format", "stream-json", "--verbose"]

    if model_override:
        cmd.extend(["--model", model_override])
    if max_turns is not None:
        cmd.extend(["--max-turns", str(max_turns)])

    # 4. Prepare log file
    log_dir = run_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"task-{task.id}.log"
    task.log_path = log_path

    # 5. Launch process — stdin always piped for prompt delivery
    # On Windows, CREATE_NEW_PROCESS_GROUP enables graceful shutdown via
    # CTRL_BREAK_EVENT instead of TerminateProcess hard-kill.
    # If api_key is provided, inject it into subprocess env only (not global os.environ).
    creation_flags = 0
    if sys.platform == "win32":
        creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess_env = None
    if api_key:
        subprocess_env = {**os.environ, "ANTHROPIC_API_KEY": api_key}
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(worktree_path),
            env=subprocess_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            stdin=asyncio.subprocess.PIPE,
            creationflags=creation_flags,
        )
    except FileNotFoundError:
        return AgentResult(
            task_id=task.id,
            status="failed",
            fail_reason="'claude' CLI not found. Install Claude Code and ensure it's in PATH.",
        )
    except OSError as e:
        return AgentResult(
            task_id=task.id,
            status="failed",
            fail_reason=f"failed to launch claude: {e}",
        )

    # Write PID file for cancellation support
    pid_dir = run_dir / "pids"
    pid_dir.mkdir(parents=True, exist_ok=True)
    pid_path = pid_dir / f"task-{task.id}.pid"
    try:
        pid_path.write_text(str(proc.pid), encoding="utf-8")
    except OSError:
        pass  # Best effort

    try:
        # Send prompt via stdin pipe (with timeout to avoid hanging on drain)
        if proc.stdin is None:
            raise RuntimeError("subprocess stdin pipe was not created")
        try:
            proc.stdin.write(prompt.encode("utf-8"))
            await asyncio.wait_for(proc.stdin.drain(), timeout=30)
        finally:
            proc.stdin.close()
            try:
                await asyncio.wait_for(proc.stdin.wait_closed(), timeout=5)
            except (TimeoutError, OSError):
                pass

        # 5. Stream stdout line by line (wrapped in timeout)
        if proc.stdout is None:
            raise RuntimeError("subprocess stdout pipe was not created")
        last_lines: collections.deque[str] = collections.deque(maxlen=5)  # keep last N lines for error context
        try:
            async with asyncio.timeout(timeout):
                with open(log_path, "a", encoding="utf-8") as log_file:
                    async for raw_line in proc.stdout:
                        line = raw_line.decode("utf-8", errors="replace")
                        log_file.write(line)
                        log_file.flush()
                        stripped = line.strip()
                        if stripped:
                            last_lines.append(stripped)

                        for event in parser.feed(line):
                            if dashboard:
                                dashboard.update(task.id, event)
                            if event.kind == "text" and event.summary:
                                output_texts.append(event.summary)
        except TimeoutError:
            proc.terminate()
            try:
                async with asyncio.timeout(3):
                    await proc.wait()
            except TimeoutError:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                await proc.wait()
            summary = "\n".join(output_texts[-5:])
            if memory and summary:
                memory.write(task.id, summary)
            if dashboard:
                dashboard.set_task_status(task.id, "failed", fail_reason="timeout")
            return AgentResult(
                task_id=task.id, status="failed", fail_reason="timeout",
                output_summary=summary,
            )

        # 6. Wait for process to fully exit and check return code
        await proc.wait()
        if proc.returncode != 0:
            fail_reason = f"claude exited with code {proc.returncode}"
            if last_lines:
                tail = "; ".join(list(last_lines)[-3:])
                fail_reason += f" — {tail[:200]}"
            summary = "\n".join(output_texts[-5:])
            if memory and summary:
                memory.write(task.id, summary)
            if dashboard:
                dashboard.set_task_status(task.id, "failed", fail_reason=fail_reason)
            return AgentResult(
                task_id=task.id, status="failed", fail_reason=fail_reason,
                output_summary=summary,
            )

        # 7. Commit changes
        result = await _commit_result(task, worktree_path, dashboard)
        result.output_summary = "\n".join(output_texts[-10:])

        # 8. Propagate token usage from dashboard
        if dashboard and task.id in dashboard.tasks:
            tp = dashboard.tasks[task.id]
            result.tokens_in = tp.tokens_in
            result.tokens_out = tp.tokens_out

        # 9. Write memory
        if memory and result.output_summary:
            memory.write(task.id, result.output_summary)

        return result
    finally:
        # Truncate oversized log files (keep last 80% if > 5MB)
        _truncate_jsonl_if_large(log_path, 5 * 1024 * 1024, 0.8)
        # Clean up PID file
        try:
            pid_path.unlink(missing_ok=True)
        except OSError:
            pass


async def _run_git_async(
    *args: str,
    cwd: Path,
    timeout: float = 60,
) -> tuple[int, str, str]:
    """Run a git command asynchronously with timeout.

    Returns (returncode, stdout, stderr).
    Raises RuntimeError on timeout.
    """
    from cagent.git_utils import run_git_async

    result = await run_git_async(*args, cwd=cwd, check=False, timeout=timeout)
    return result.returncode, result.stdout, result.stderr


async def _git_op(
    op_name: str,
    *args: str,
    cwd: Path,
    task: Task,
    dashboard: Dashboard | None,
    timeout: float = 60,
) -> tuple[int, str, str] | AgentResult:
    """Run a git operation with automatic GitTimeoutError handling.

    op_name is both the git subcommand and the human-readable label.
    Returns (returncode, stdout, stderr) on success.
    Returns AgentResult(failed) on timeout.
    """
    try:
        rc, stdout, stderr = await _run_git_async(op_name, *args, cwd=cwd, timeout=timeout)
    except GitTimeoutError:
        reason = f"git {op_name} timed out"
        if dashboard:
            dashboard.set_task_status(task.id, "failed", fail_reason=reason)
        return AgentResult(task_id=task.id, status="failed", fail_reason=reason)
    return rc, stdout, stderr


async def _git_op_checked(
    op_name: str,
    *args: str,
    cwd: Path,
    task: Task,
    dashboard: Dashboard | None,
    timeout: float = 60,
) -> tuple[bool, str, str] | AgentResult:
    """Run a git operation, returning (True, stdout, stderr) on success.

    Returns AgentResult(failed) on timeout or non-zero exit.
    """
    result = await _git_op(op_name, *args, cwd=cwd, task=task, dashboard=dashboard, timeout=timeout)
    if isinstance(result, AgentResult):
        return result
    rc, stdout, stderr = result
    if rc != 0:
        err = stderr.strip()
        reason = f"git {op_name} failed: {err}" if err else f"git {op_name} failed"
        if dashboard:
            dashboard.set_task_status(task.id, "failed", fail_reason=reason)
        return AgentResult(task_id=task.id, status="failed", fail_reason=reason)
    return True, stdout, stderr


async def _commit_result(
    task: Task,
    worktree_path: Path,
    dashboard: Dashboard | None,
) -> AgentResult:
    """Check for changes in worktree and commit if any."""
    # Check git status
    result = await _git_op("status", "--porcelain", cwd=worktree_path, task=task, dashboard=dashboard)
    if isinstance(result, AgentResult):
        return result
    _, stdout, _ = result

    status_output = stdout.strip()

    if not status_output:
        # No changes
        if dashboard:
            dashboard.set_task_status(task.id, "noop")
        return AgentResult(task_id=task.id, status="noop")

    # Stage and commit
    first_line = task.prompt.strip().split("\n")[0][:72] or "(no description)"
    commit_msg = f"task {task.id}: {first_line}"

    # Exclude .claude/ sandbox files from commit
    claude_dir = worktree_path / ".claude"
    sandbox_files = [
        claude_dir / "settings.local.json",
        claude_dir / "hooks" / "cagent-guard.py",
    ]
    for f in sandbox_files:
        if f.exists():
            f.unlink()
    hooks_dir = claude_dir / "hooks"
    if hooks_dir.exists() and not any(hooks_dir.iterdir()):
        hooks_dir.rmdir()

    # Restore tracked .claude/ and .gitignore from base (best-effort, may not exist)
    for path in (".claude/", ".gitignore"):
        r = await _git_op("checkout", "HEAD", "--", path, cwd=worktree_path, task=task, dashboard=dashboard)
        if isinstance(r, AgentResult):
            return r

    # Verify sandbox files are cleared before staging
    for f in sandbox_files:
        if f.exists():
            try:
                f.unlink()
            except OSError:
                pass

    # git add -A
    r = await _git_op_checked("add", "-A", cwd=worktree_path, task=task, dashboard=dashboard)
    if isinstance(r, AgentResult):
        return r

    # git commit
    r = await _git_op_checked("commit", "-m", commit_msg, cwd=worktree_path, task=task, dashboard=dashboard)
    if isinstance(r, AgentResult):
        return r

    # Get commit SHA
    r = await _git_op_checked("rev-parse", "HEAD", cwd=worktree_path, task=task, dashboard=dashboard)
    if isinstance(r, AgentResult):
        return r
    _, sha_out, _ = r
    commit_sha = sha_out.strip()

    if dashboard:
        dashboard.set_task_status(task.id, "done", commit_sha=commit_sha)

    return AgentResult(task_id=task.id, status="done", commit_sha=commit_sha)
