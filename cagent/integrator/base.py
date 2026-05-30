"""Shared integrator utilities — git helpers, claude agent runner, conflict resolution."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Literal

from cagent.agent import _resolve_claude
from cagent.git_utils import GitResult
from cagent.memory import RunMemory
from cagent.progress import Dashboard, Event, EventParser
from cagent.safety import prepare_sandbox
from cagent.tasks import Task

__all__ = [
    "_run_git",
    "_run_shell_cmd",
    "_run_claude_agent",
    "_validate_cmd_str",
    "_has_conflict_markers",
    "_is_conflict_xy",
    "_resolve_conflicts",
    "_abort_operation",
    "_report",
    "_post_integrate_validate",
]


async def _run_git(
    *args: str,
    cwd: Path,
    env: dict[str, str] | None = None,
    check: bool = True,
    timeout: float = 60,
) -> GitResult:
    """Run a git command and return a GitResult."""
    from cagent.git_utils import run_git_async

    return await run_git_async(
        *args, cwd=cwd, env=env, check=check, timeout=timeout
    )


def _validate_cmd_str(cmd_str: str) -> bool:
    """Validate that a command string does not contain control characters.

    This function validates trusted input from CLI arguments (--post-integrate-cmd).
    Rejects control characters, null bytes, backticks, $(...) command
    substitution, and shell metacharacters (|, ;, &, >, <) to prevent
    injection via task prompts.
    """
    if not cmd_str:
        return False
    if any(c in cmd_str for c in '\n\r\t\x00'):
        return False
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in cmd_str):
        return False
    if '`' in cmd_str:
        return False
    if '$(' in cmd_str:
        return False
    # Reject shell metacharacters that enable command chaining / redirection.
    # This prevents injection like "legit_cmd; rm -rf /" or "cmd | malicious".
    if any(c in cmd_str for c in '|;&><'):
        return False
    return True


async def _run_shell_cmd(
    cmd_str: str,
    cwd: Path,
    timeout: float = 300,
) -> tuple[int, str]:
    """Run a shell command string and return (returncode, combined output)."""
    if not _validate_cmd_str(cmd_str):
        return 1, "Command rejected: contains disallowed characters. Only alphanumeric, spaces, and common shell characters are allowed."

    if sys.platform == "win32":
        proc = await asyncio.create_subprocess_exec(
            "cmd", "/c", cmd_str,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    else:
        proc = await asyncio.create_subprocess_exec(
            "sh", "-c", cmd_str,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    try:
        stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()
        return 1, f"Command timed out after {timeout}s"
    rc = proc.returncode if proc.returncode is not None else -1
    return rc, stdout_bytes.decode("utf-8", errors="replace")


async def _run_claude_agent(
    prompt: str,
    worktree_path: Path,
    run_dir: Path,
    model_override: str | None,
    timeout: int,
    dashboard: Dashboard | None,
    task_id: str = "_integrator",
    api_key: str | None = None,
) -> int | None:
    """Spawn a claude -p subprocess, stream output, return exit code.

    Returns the process exit code, or None if the process could not be started
    or timed out. Caller is responsible for calling prepare_sandbox() beforehand.
    """

    cmd = [_resolve_claude(), "-p", "-"]
    cmd.extend(["--permission-mode", "acceptEdits", "--output-format", "stream-json", "--verbose"])
    if model_override:
        cmd.extend(["--model", model_override])

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
        return None
    except OSError:
        return None

    if proc.stdin is None:
        return None
    try:
        proc.stdin.write(prompt.encode("utf-8"))
        await asyncio.wait_for(proc.stdin.drain(), timeout=30)
    except (OSError, BrokenPipeError, asyncio.TimeoutError):
        # stdin write failed — kill the process to avoid zombie (Phase 90.M9).
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()
        return None
    finally:
        proc.stdin.close()
        try:
            await asyncio.wait_for(proc.stdin.wait_closed(), timeout=5)
        except (TimeoutError, OSError):
            pass

    log_path = run_dir / "logs" / f"task-{task_id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    parser = EventParser()
    if proc.stdout is None:
        return None
    try:
        async with asyncio.timeout(timeout):
            with open(log_path, "a", encoding="utf-8") as f:
                async for raw_line in proc.stdout:
                    line = raw_line.decode("utf-8", errors="replace")
                    f.write(line)
                    f.flush()
                    for evt in parser.feed(line):
                        if dashboard:
                            dashboard.update(task_id, evt)
    except TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()
        return None

    await proc.wait()
    return proc.returncode


def _is_conflict_xy(xy: str) -> bool:
    """Check if a 2-char git porcelain XY status indicates a conflict.

    Conflicts are indicated by 'U' (unmerged) in either position, or by
    the special double-letter codes DD (both deleted) and AA (both added).
    """
    return "U" in xy or xy in ("DD", "AA")


def _has_conflict_markers(status_output: str) -> bool:
    """Check if git porcelain status contains any conflict markers."""
    for line in status_output.splitlines():
        if len(line) < 2:
            continue
        if _is_conflict_xy(line[:2]):
            return True
    return False


def _report(
    dashboard: Dashboard | None,
    kind: Literal["start", "tool_use", "tool_result", "text", "thinking", "denied", "done", "error"],
    summary: str,
) -> None:
    """Send an event to the dashboard if available."""
    if dashboard:
        dashboard.update("_integrator", Event(ts=time.time(), kind=kind, summary=summary, raw={}))


async def _abort_operation(mode: str, worktree_path: Path) -> None:
    """Abort the current git operation based on mode."""
    if mode == "cherry-pick":
        await _run_git("cherry-pick", "--abort", cwd=worktree_path, check=False)
    elif mode == "merge":
        await _run_git("merge", "--abort", cwd=worktree_path, check=False)
    elif mode == "rebase":
        await _run_git("rebase", "--abort", cwd=worktree_path, check=False)


async def _resolve_conflicts(
    task: Task,
    integrated_tasks: list[Task],
    worktree_path: Path,
    run_dir: Path,
    integrator_model_override: str | None,
    timeout: int,
    dashboard: Dashboard | None,
    memory: RunMemory | None = None,
    completion_mode: str = "cherry-pick",
    api_key: str | None = None,
) -> bool:
    """Use an integrator agent to resolve conflicts.

    Args:
        completion_mode: How to complete after conflict resolution.
            - "cherry-pick": run cherry-pick --continue
            - "merge": run commit --no-edit (for merge conflicts)
            - "rebase": run rebase --continue (for rebase conflicts)
    """
    status = await _run_git("status", "--porcelain", cwd=worktree_path, check=False)
    conflict_files = []
    for line in status.stdout.splitlines():
        if len(line) >= 3 and _is_conflict_xy(line[:2]):
            raw = line[3:].strip()
            if " -> " in raw:
                raw = raw.split(" -> ", 1)[1]
            conflict_files.append(raw)

    if not conflict_files:
        return False

    _MAX_SUMMARIES_CHARS = 2000
    merged_summaries = ""
    for t in integrated_tasks:
        if t == task:
            continue
        task_memory = memory.read(t.id) if memory else ""
        if task_memory:
            merged_summaries += f"  - task {t.id} ({t.prompt[:50]}):\n    {task_memory[:300]}\n"
        else:
            merged_summaries += f"  - task {t.id}: {t.prompt.split(chr(10))[0][:80]}\n"
        if len(merged_summaries) > _MAX_SUMMARIES_CHARS:
            merged_summaries += "  - ...(truncated)\n"
            break

    conflict_list = "\n".join(f"  - {f}" for f in conflict_files)

    if merged_summaries:
        context_block = (
            f"The current task (task {task.id}) has conflicts with already-merged tasks:\n"
            f"{merged_summaries}"
        )
    else:
        context_block = (
            f"The current task (task {task.id}) conflicts with the base branch.\n"
            f"There are no previously merged tasks — this is the first integration."
        )

    _mode_labels = {"cherry-pick": "cherry-pick", "merge": "merge", "rebase": "rebase"}
    operation = _mode_labels.get(completion_mode, completion_mode)
    prompt = (
        f"You are resolving merge conflicts in a {operation} operation.\n\n"
        f"{context_block}\n\n"
        f"Conflicting files:\n{conflict_list}\n\n"
        f"Current task prompt: {task.prompt}\n\n"
        f"Please resolve ALL conflict markers in the conflicting files. "
        f"Preserve the intent of both sides. After resolving, the files should "
        f"have no <<<<<<< ======= >>>>>>> markers."
    )

    if dashboard:
        event = Event(
            ts=time.time(),
            kind="text",
            summary=f"{operation} task {task.id} → conflict, launching integrator",
            raw={},
        )
        dashboard.update("_integrator", event)

    prepare_sandbox(worktree_path)

    rc = await _run_claude_agent(
        prompt=prompt,
        worktree_path=worktree_path,
        run_dir=run_dir,
        model_override=integrator_model_override,
        timeout=timeout,
        dashboard=dashboard,
        api_key=api_key,
    )

    if rc is None or rc != 0:
        if rc is not None and dashboard:
            event = Event(
                ts=time.time(),
                kind="error",
                summary=f"integrator agent exited with code {rc}",
                raw={},
            )
            dashboard.update("_integrator", event)
        await _abort_operation(completion_mode, worktree_path)
        return False

    # Stage all files first so `git grep` also covers newly-created untracked
    # files that the agent may have written conflict markers into (Phase 89.5).
    await _run_git("add", "-A", cwd=worktree_path, check=False)

    # Detect residual conflict markers. Only the start (<<<<<<<), ancestor
    # (|||||||) and end (>>>>>>>) markers are line-anchored and followed by a
    # space or end-of-line in real git conflicts; a genuine conflict always
    # contains the <<<<<<< / >>>>>>> pair. The bare ======= separator is NOT
    # matched on its own — markdown setext headings and ASCII banners legitimately
    # contain lines of seven-or-more '=' characters, which previously caused false
    # positives that aborted otherwise-successful conflict resolutions.
    grep_result = await _run_git(
        "grep", "-rl", "-E", r"^(<{7}|>{7}|\|{7})( |$)",
        cwd=worktree_path,
        check=False,
    )
    if grep_result.returncode == 0:
        if dashboard:
            event = Event(
                ts=time.time(),
                kind="error",
                summary=f"conflict markers remain in: {grep_result.stdout.strip()[:100]}",
                raw={},
            )
            dashboard.update("_integrator", event)
        await _abort_operation(completion_mode, worktree_path)
        return False

    claude_dir = worktree_path / ".claude"
    if claude_dir.exists():
        try:
            shutil.rmtree(claude_dir)
        except OSError:
            pass

    env_continue = {**os.environ, "GIT_EDITOR": "true"}
    # Note: `git add -A` already done above before grep (Phase 89.5).
    # The second add was redundant (Phase 90.M8 fix).

    if completion_mode == "cherry-pick":
        try:
            await _run_git("cherry-pick", "--continue", cwd=worktree_path, env=env_continue)
        except RuntimeError:
            await _run_git("cherry-pick", "--abort", cwd=worktree_path, check=False)
            return False
    elif completion_mode == "merge":
        try:
            await _run_git("commit", "--no-edit", cwd=worktree_path, env=env_continue)
        except RuntimeError:
            await _run_git("merge", "--abort", cwd=worktree_path, check=False)
            return False
    elif completion_mode == "rebase":
        try:
            await _run_git("rebase", "--continue", cwd=worktree_path, env=env_continue)
        except RuntimeError:
            await _run_git("rebase", "--abort", cwd=worktree_path, check=False)
            return False

    if memory:
        memory.append("_integrator", (
            f"Resolved conflict for task {task.id} ({task.prompt[:60]})\n"
            f"Conflicting files: {', '.join(conflict_files)}\n"
            f"Preserved intent of both sides."
        ))

    result = await _run_git("rev-parse", "HEAD", cwd=worktree_path, check=False)
    if result.returncode != 0:
        return False
    task.commit_sha = result.stdout.strip()
    task.status = "done"
    if dashboard:
        dashboard.set_task_status(task.id, "done", commit_sha=task.commit_sha)

    return True


async def _post_integrate_validate(
    cmd_str: str,
    worktree_path: Path,
    run_dir: Path,
    integrator_model_override: str | None,
    timeout: int,
    dashboard: Dashboard | None,
    max_rounds: int = 2,
    api_key: str | None = None,
) -> bool:
    """Run post-integration command; on failure, launch integrator agent to fix, retry.

    Returns True if the command eventually passes, False after max_rounds failures.
    """
    for round_num in range(1, max_rounds + 1):
        if dashboard:
            event = Event(
                ts=time.time(),
                kind="text",
                summary=f"post-integrate-cmd round {round_num}: {cmd_str}",
                raw={},
            )
            dashboard.update("_integrator", event)

        returncode, output = await _run_shell_cmd(cmd_str, worktree_path, timeout=timeout)

        if returncode == 0:
            if dashboard:
                event = Event(
                    ts=time.time(),
                    kind="text",
                    summary=f"post-integrate-cmd passed (round {round_num})",
                    raw={},
                )
                dashboard.update("_integrator", event)
            return True

        if dashboard:
            event = Event(
                ts=time.time(),
                kind="error",
                summary=f"post-integrate-cmd failed (round {round_num}, exit {returncode})",
                raw={},
            )
            dashboard.update("_integrator", event)

        if round_num >= max_rounds:
            break

        repair_prompt = (
            f"The post-integration validation command failed.\n\n"
            f"Command: {cmd_str}\n"
            f"Exit code: {returncode}\n"
            f"Output (last 3000 chars):\n{output[-3000:]}\n\n"
            f"Please fix the code so the command passes. "
            f"Do NOT modify the test command itself — fix the source code."
        )

        prepare_sandbox(worktree_path)

        rc = await _run_claude_agent(
            prompt=repair_prompt,
            worktree_path=worktree_path,
            run_dir=run_dir,
            model_override=integrator_model_override,
            timeout=timeout,
            dashboard=dashboard,
            api_key=api_key,
        )

        if rc is None or rc != 0:
            if rc is not None and dashboard:
                event = Event(
                    ts=time.time(),
                    kind="error",
                    summary=f"repair agent exited with code {rc}",
                    raw={},
                )
                dashboard.update("_integrator", event)
            break

        try:
            await _run_git("add", "-A", cwd=worktree_path)
            status = await _run_git("status", "--porcelain", cwd=worktree_path, check=False)
            if status.stdout.strip():
                await _run_git("commit", "-m", f"fix: post-integrate-cmd repair round {round_num}", cwd=worktree_path, check=False)
            else:
                if dashboard:
                    event = Event(
                        ts=time.time(),
                        kind="text",
                        summary=f"repair round {round_num}: agent made no changes, skipping commit",
                        raw={},
                    )
                    dashboard.update("_integrator", event)
        except RuntimeError:
            break

    return False
