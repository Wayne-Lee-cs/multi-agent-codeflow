"""Agent subprocess management — spawn claude -p, stream events, commit results."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from cagent.progress import Dashboard, EventParser
from cagent.safety import prepare_sandbox
from cagent.tasks import Task


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


async def run_agent(
    task: Task,
    worktree_path: Path,
    run_dir: Path,
    timeout: int = 1800,
    model_override: str | None = None,
    dashboard: Dashboard | None = None,
) -> AgentResult:
    """Run a claude -p subprocess for a single task in its worktree.

    Returns an AgentResult with the outcome.
    """
    parser = EventParser()

    # 1. Inject safety sandbox
    prepare_sandbox(worktree_path)

    # 2. Build command
    prompt = task.prompt
    use_stdin = len(prompt) > 8000 or '"' in prompt or '\\' in prompt or '\n' in prompt

    claude_bin = _resolve_claude()
    cmd: list[str] = [claude_bin, "-p"]
    if use_stdin:
        cmd.append("-")
    else:
        cmd.append(prompt)
    cmd.extend(["--permission-mode", "acceptEdits", "--output-format", "stream-json", "--verbose"])

    if model_override:
        cmd.extend(["--model", model_override])

    # 3. Prepare log file
    log_dir = run_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"task-{task.id}.log"
    task.log_path = log_path

    # 4. Launch process
    env = os.environ.copy()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(worktree_path),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            stdin=asyncio.subprocess.PIPE if use_stdin else asyncio.subprocess.DEVNULL,
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

    # Send prompt via stdin if needed
    if use_stdin:
        if proc.stdin is None:
            raise RuntimeError("subprocess stdin pipe was not created")
        proc.stdin.write(prompt.encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()

    # 5. Stream stdout line by line (wrapped in timeout)
    if proc.stdout is None:
        raise RuntimeError("subprocess stdout pipe was not created")
    try:
        async with asyncio.timeout(timeout):
            with open(log_path, "a", encoding="utf-8") as log_file:
                async for raw_line in proc.stdout:
                    line = raw_line.decode("utf-8", errors="replace")
                    log_file.write(line)
                    log_file.flush()

                    event = parser.feed(line)
                    if event and dashboard:
                        dashboard.update(task.id, event)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        if dashboard:
            dashboard.set_task_status(task.id, "failed", fail_reason="timeout")
        return AgentResult(task_id=task.id, status="failed", fail_reason="timeout")

    # 7. Commit changes
    return await _commit_result(task, worktree_path, dashboard)


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

    # git add -A
    add_proc = await asyncio.create_subprocess_exec(
        "git", "add", "-A",
        cwd=str(worktree_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await add_proc.wait()

    # git commit
    commit_proc = await asyncio.create_subprocess_exec(
        "git", "commit", "-m", commit_msg,
        cwd=str(worktree_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    commit_stdout, commit_stderr = await commit_proc.communicate()

    if commit_proc.returncode != 0:
        err = commit_stderr.decode("utf-8", errors="replace")
        if dashboard:
            dashboard.set_task_status(task.id, "failed", fail_reason=f"git commit failed: {err}")
        return AgentResult(task_id=task.id, status="failed", fail_reason="git commit failed")

    # Get commit SHA
    sha_proc = await asyncio.create_subprocess_exec(
        "git", "rev-parse", "HEAD",
        cwd=str(worktree_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    sha_out, _ = await sha_proc.communicate()
    commit_sha = sha_out.decode("utf-8").strip()

    if dashboard:
        dashboard.set_task_status(task.id, "done", commit_sha=commit_sha)

    return AgentResult(task_id=task.id, status="done", commit_sha=commit_sha)
