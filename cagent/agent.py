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

from cagent.git_utils import GitTimeoutError
from cagent.memory import RunMemory
from cagent.progress import Dashboard, EventParser
from cagent.safety import prepare_sandbox
from cagent.tasks import Task


@functools.lru_cache(maxsize=1)
def _resolve_claude() -> str:
    """Find the claude CLI executable, handling Windows .cmd extension."""
    for name in ("claude", "claude.cmd", "claude.exe"):
        path = shutil.which(name)
        if path:
            return path
    return "claude"  # fallback, will fail at launch with clear error


_CAGENT_GITIGNORE_MARKER = "# cagent worktree exclusions"
_CAGENT_GITIGNORE_LINES = ".claude/\n.env\nnode_modules/\n__pycache__/\n*.pyc\n.venv/\n"


@dataclass
class AgentResult:
    task_id: str
    status: str  # "done", "failed", "noop"
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
    creation_flags = 0
    if sys.platform == "win32":
        creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(worktree_path),
            env=None,
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
        # Send prompt via stdin pipe
        if proc.stdin is None:
            raise RuntimeError("subprocess stdin pipe was not created")
        try:
            proc.stdin.write(prompt.encode("utf-8"))
            await proc.stdin.drain()
        finally:
            proc.stdin.close()
            await proc.stdin.wait_closed()

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


async def _commit_result(
    task: Task,
    worktree_path: Path,
    dashboard: Dashboard | None,
) -> AgentResult:
    """Check for changes in worktree and commit if any."""
    # Check git status
    try:
        returncode, stdout, stderr = await _run_git_async(
            "status", "--porcelain", cwd=worktree_path, timeout=60
        )
    except GitTimeoutError:
        if dashboard:
            dashboard.set_task_status(task.id, "failed", fail_reason="git status timed out")
        return AgentResult(task_id=task.id, status="failed", fail_reason="git status timed out")

    status_output = stdout.strip()

    if not status_output:
        # No changes
        if dashboard:
            dashboard.set_task_status(task.id, "noop")
        return AgentResult(task_id=task.id, status="noop")

    # Stage and commit
    first_line = task.prompt.strip().split("\n")[0][:72]
    commit_msg = f"task {task.id}: {first_line}"

    # Exclude .claude/ sandbox files from commit. The sandbox creates
    # .claude/settings.local.json and .claude/hooks/cagent-guard.py that
    # should not be committed. We only delete these known sandbox artifacts,
    # preserving any other legitimate .claude/ files (settings.json, commands/).
    claude_dir = worktree_path / ".claude"
    sandbox_files = [
        claude_dir / "settings.local.json",
        claude_dir / "hooks" / "cagent-guard.py",
    ]
    for f in sandbox_files:
        if f.exists():
            f.unlink()
    # Remove hooks dir if empty
    hooks_dir = claude_dir / "hooks"
    if hooks_dir.exists() and not any(hooks_dir.iterdir()):
        hooks_dir.rmdir()
    # Restore tracked .claude/ files from base (if any were deleted)
    try:
        rc, _, _ = await _run_git_async(
            "checkout", "HEAD", "--", ".claude/", cwd=worktree_path, timeout=60
        )
    except GitTimeoutError:
        if dashboard:
            dashboard.set_task_status(task.id, "failed", fail_reason="git checkout .claude/ timed out")
        return AgentResult(task_id=task.id, status="failed", fail_reason="git checkout .claude/ timed out")

    # Restore .gitignore to base (sandbox may have modified it)
    try:
        rc, _, _ = await _run_git_async(
            "checkout", "HEAD", "--", ".gitignore", cwd=worktree_path, timeout=60
        )
    except GitTimeoutError:
        if dashboard:
            dashboard.set_task_status(task.id, "failed", fail_reason="git checkout .gitignore timed out")
        return AgentResult(task_id=task.id, status="failed", fail_reason="git checkout .gitignore timed out")

    # Verify sandbox files are cleared before staging
    for f in sandbox_files:
        if f.exists():
            # Force remove if still present
            try:
                f.unlink()
            except OSError:
                pass

    # git add -A
    try:
        rc, add_out, add_err = await _run_git_async(
            "add", "-A", cwd=worktree_path, timeout=60
        )
    except GitTimeoutError:
        if dashboard:
            dashboard.set_task_status(task.id, "failed", fail_reason="git add -A timed out")
        return AgentResult(task_id=task.id, status="failed", fail_reason="git add -A timed out")
    if rc != 0:
        if dashboard:
            dashboard.set_task_status(task.id, "failed", fail_reason="git add -A failed")
        return AgentResult(task_id=task.id, status="failed", fail_reason="git add -A failed")

    # git commit
    try:
        rc, commit_out, commit_err = await _run_git_async(
            "commit", "-m", commit_msg, cwd=worktree_path, timeout=60
        )
    except GitTimeoutError:
        if dashboard:
            dashboard.set_task_status(task.id, "failed", fail_reason="git commit timed out")
        return AgentResult(task_id=task.id, status="failed", fail_reason="git commit timed out")
    if rc != 0:
        err = commit_err.strip()
        fail_reason = f"git commit failed: {err}" if err else "git commit failed"
        if dashboard:
            dashboard.set_task_status(task.id, "failed", fail_reason=fail_reason)
        return AgentResult(task_id=task.id, status="failed", fail_reason=fail_reason)

    # Get commit SHA
    try:
        rc, sha_out, sha_err = await _run_git_async(
            "rev-parse", "HEAD", cwd=worktree_path, timeout=60
        )
    except GitTimeoutError:
        if dashboard:
            dashboard.set_task_status(task.id, "failed", fail_reason="git rev-parse HEAD timed out")
        return AgentResult(task_id=task.id, status="failed", fail_reason="git rev-parse HEAD timed out")
    if rc != 0:
        err = sha_err.strip()
        fail_reason = f"git rev-parse HEAD failed: {err}" if err else "git rev-parse HEAD failed"
        if dashboard:
            dashboard.set_task_status(task.id, "failed", fail_reason=fail_reason)
        return AgentResult(task_id=task.id, status="failed", fail_reason=fail_reason)
    commit_sha = sha_out.strip()

    if dashboard:
        dashboard.set_task_status(task.id, "done", commit_sha=commit_sha)

    return AgentResult(task_id=task.id, status="done", commit_sha=commit_sha)
