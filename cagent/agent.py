"""Agent subprocess management — spawn claude -p, stream events, commit results."""

from __future__ import annotations

import asyncio
import functools
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

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
) -> AgentResult:
    """Run a claude -p subprocess for a single task in its worktree.

    Returns an AgentResult with the outcome.
    """
    parser = EventParser()
    output_texts: list[str] = []  # accumulate text events for memory

    # 1. Inject safety sandbox
    prepare_sandbox(worktree_path)

    # 2. Build command — always use stdin pipe for prompt delivery.
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

    # 3. Prepare log file
    log_dir = run_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"task-{task.id}.log"
    task.log_path = log_path

    # 4. Launch process — stdin always piped for prompt delivery
    env = os.environ.copy()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(worktree_path),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            stdin=asyncio.subprocess.PIPE,
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
        last_lines: list[str] = []  # keep last N lines for error context
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
                            if len(last_lines) > 5:
                                last_lines.pop(0)

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
                proc.kill()
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
                tail = "; ".join(last_lines[-3:])
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


async def _commit_result(
    task: Task,
    worktree_path: Path,
    dashboard: Dashboard | None,
) -> AgentResult:
    """Check for changes in worktree and commit if any."""
    # Check git status
    result = await asyncio.create_subprocess_exec(
        "git", "status", "--porcelain",
        cwd=str(worktree_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await result.communicate()
    status_output = stdout.decode("utf-8").strip()

    if not status_output:
        # No changes
        if dashboard:
            dashboard.set_task_status(task.id, "noop")
        return AgentResult(task_id=task.id, status="noop")

    # Stage and commit
    first_line = task.prompt.split("\n")[0][:72]
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
    checkout_claude = await asyncio.create_subprocess_exec(
        "git", "checkout", "HEAD", "--", ".claude/",
        cwd=str(worktree_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await checkout_claude.communicate()

    # Restore .gitignore to base (sandbox may have modified it)
    checkout_gitignore = await asyncio.create_subprocess_exec(
        "git", "checkout", "HEAD", "--", ".gitignore",
        cwd=str(worktree_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await checkout_gitignore.communicate()

    # git add -A
    add_proc = await asyncio.create_subprocess_exec(
        "git", "add", "-A",
        cwd=str(worktree_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await add_proc.communicate()
    if add_proc.returncode != 0:
        if dashboard:
            dashboard.set_task_status(task.id, "failed", fail_reason="git add -A failed")
        return AgentResult(task_id=task.id, status="failed", fail_reason="git add -A failed")

    # git commit
    commit_proc = await asyncio.create_subprocess_exec(
        "git", "commit", "-m", commit_msg,
        cwd=str(worktree_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    commit_stdout, commit_stderr = await commit_proc.communicate()

    if commit_proc.returncode != 0:
        err = commit_stderr.decode("utf-8", errors="replace").strip()
        fail_reason = f"git commit failed: {err}" if err else "git commit failed"
        if dashboard:
            dashboard.set_task_status(task.id, "failed", fail_reason=fail_reason)
        return AgentResult(task_id=task.id, status="failed", fail_reason=fail_reason)

    # Get commit SHA
    sha_proc = await asyncio.create_subprocess_exec(
        "git", "rev-parse", "HEAD",
        cwd=str(worktree_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    sha_out, sha_err = await sha_proc.communicate()
    if sha_proc.returncode != 0:
        err = sha_err.decode("utf-8", errors="replace").strip()
        fail_reason = f"git rev-parse HEAD failed: {err}" if err else "git rev-parse HEAD failed"
        if dashboard:
            dashboard.set_task_status(task.id, "failed", fail_reason=fail_reason)
        return AgentResult(task_id=task.id, status="failed", fail_reason=fail_reason)
    commit_sha = sha_out.decode("utf-8").strip()

    if dashboard:
        dashboard.set_task_status(task.id, "done", commit_sha=commit_sha)

    return AgentResult(task_id=task.id, status="done", commit_sha=commit_sha)
