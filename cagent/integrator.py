"""Integrator — cherry-pick task commits into a unified integration branch."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from cagent.agent import _resolve_claude
from cagent.progress import Dashboard, Event, EventParser
from cagent.safety import prepare_sandbox
from cagent.tasks import Task


async def integrate(
    tasks: list[Task],
    run_dir: Path,
    base_sha: str,
    repo_root: Path,
    squash: bool = False,
    integrator_model_override: str | None = None,
    timeout: int = 1800,
    dashboard: Dashboard | None = None,
) -> str:
    """Cherry-pick all done task commits into an integration branch.

    Returns the integration branch tip SHA.
    """
    run_id = run_dir.name
    integration_branch = f"cagent/{run_id}/integration"
    worktree_path = repo_root / ".cagent" / "worktrees" / run_id / "_integration"

    # Create integration worktree
    from cagent.worktree import create_worktree
    create_worktree(repo_root, worktree_path, integration_branch, base_sha)

    # Inject safety sandbox
    prepare_sandbox(worktree_path)

    done_tasks = [t for t in tasks if t.status == "done" and t.commit_sha]
    if not done_tasks:
        return base_sha

    integrated = []
    failed = []
    for task in done_tasks:
        success = await _cherry_pick_one(
            task=task,
            all_tasks=done_tasks,
            worktree_path=worktree_path,
            run_dir=run_dir,
            repo_root=repo_root,
            integrator_model_override=integrator_model_override,
            timeout=timeout,
            dashboard=dashboard,
        )
        if success:
            integrated.append(task)
        else:
            failed.append(task)
            if dashboard:
                event = Event(
                    ts=time.time(),
                    kind="error",
                    summary=f"cherry-pick task {task.id} failed, skipping",
                    raw={},
                )
                dashboard.update("_integrator", event)

    if failed and not integrated:
        raise RuntimeError(
            f"All {len(failed)} cherry-picks failed. "
            f"Worktree preserved at {worktree_path} for manual inspection."
        )

    if failed:
        # Some succeeded, some failed — partial integration
        if dashboard:
            event = Event(
                ts=time.time(),
                kind="text",
                summary=f"partial integration: {len(integrated)} ok, {len(failed)} skipped",
                raw={},
            )
            dashboard.update("_integrator", event)

    # Squash if requested
    if squash:
        await _run_git("reset", "--soft", base_sha, cwd=worktree_path)
        summary_parts = [f"task {t.id}: {t.prompt.split(chr(10))[0][:50]}" for t in done_tasks]
        commit_msg = "integrate:\n" + "\n".join(f"- {s}" for s in summary_parts)
        await _run_git("commit", "-m", commit_msg, cwd=worktree_path)

    # Get final SHA
    result = await _run_git("rev-parse", "HEAD", cwd=worktree_path)
    return result.stdout.strip()


async def _cherry_pick_one(
    task: Task,
    all_tasks: list[Task],
    worktree_path: Path,
    run_dir: Path,
    repo_root: Path,
    integrator_model_override: str | None,
    timeout: int,
    dashboard: Dashboard | None,
) -> bool:
    """Cherry-pick a single task commit. Returns True on success."""
    if not task.commit_sha:
        raise RuntimeError(f"task {task.id} has no commit_sha")

    # Try cherry-pick
    proc = await asyncio.create_subprocess_exec(
        "git", "cherry-pick", task.commit_sha,
        cwd=str(worktree_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode == 0:
        return True

    # Check for conflicts using porcelain format
    status = await _run_git("status", "--porcelain", cwd=worktree_path, check=False)
    has_conflicts = any(line.startswith("UU") or line.startswith("AA") for line in status.stdout.splitlines())

    if not has_conflicts:
        # Cherry-pick failed for non-conflict reason
        await _run_git("cherry-pick", "--abort", cwd=worktree_path, check=False)
        return False

    # Resolve conflicts with integrator agent
    return await _resolve_conflicts(
        task=task,
        all_tasks=all_tasks,
        worktree_path=worktree_path,
        run_dir=run_dir,
        integrator_model_override=integrator_model_override,
        timeout=timeout,
        dashboard=dashboard,
    )


async def _resolve_conflicts(
    task: Task,
    all_tasks: list[Task],
    worktree_path: Path,
    run_dir: Path,
    integrator_model_override: str | None,
    timeout: int,
    dashboard: Dashboard | None,
) -> bool:
    """Use an integrator agent to resolve cherry-pick conflicts."""
    # Get conflict file list using porcelain format
    status = await _run_git("status", "--porcelain", cwd=worktree_path, check=False)
    conflict_files = []
    for line in status.stdout.splitlines():
        # Porcelain format: XY <path> where X or Y is U for unmerged
        if len(line) >= 3 and (line[0] == "U" or line[1] == "U"):
            conflict_files.append(line[3:].strip())
        elif line.startswith("AA"):
            conflict_files.append(line[3:].strip())

    # Build integrator prompt
    already_merged = [t for t in all_tasks if t != task and t.status == "done"]
    merged_summaries = "\n".join(
        f"  - task {t.id}: {t.prompt.split(chr(10))[0][:80]}"
        for t in already_merged
    )
    conflict_list = "\n".join(f"  - {f}" for f in conflict_files)

    prompt = (
        f"You are resolving merge conflicts in a cherry-pick operation.\n\n"
        f"The current task (task {task.id}) has conflicts with already-merged tasks:\n"
        f"{merged_summaries}\n\n"
        f"Conflicting files:\n{conflict_list}\n\n"
        f"Current task prompt: {task.prompt}\n\n"
        f"Please resolve ALL conflict markers in the conflicting files. "
        f"Preserve the intent of both sides. After resolving, the files should "
        f"have no <<<<<< ======= >>>>>> markers."
    )

    if dashboard:
        event = Event(
            ts=time.time(),
            kind="text",
            summary=f"cherry-pick task {task.id} → conflict, launching integrator",
            raw={},
        )
        dashboard.update("_integrator", event)

    # Run integrator agent
    cmd = [_resolve_claude(), "-p", "-"]
    cmd.extend(["--permission-mode", "acceptEdits", "--output-format", "stream-json", "--verbose"])
    if integrator_model_override:
        cmd.extend(["--model", integrator_model_override])

    env = os.environ.copy()
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(worktree_path),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        stdin=asyncio.subprocess.PIPE,
    )

    if proc.stdin is None:
        raise RuntimeError("subprocess stdin pipe was not created")
    proc.stdin.write(prompt.encode("utf-8"))
    await proc.stdin.drain()
    proc.stdin.close()

    # Drain output (log it) with timeout
    log_path = run_dir / "logs" / "task-_integrator.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    parser = EventParser()
    if proc.stdout is None:
        raise RuntimeError("subprocess stdout pipe was not created")
    try:
        async with asyncio.timeout(timeout):
            with open(log_path, "a", encoding="utf-8") as f:
                async for raw_line in proc.stdout:
                    line = raw_line.decode("utf-8", errors="replace")
                    f.write(line)
                    f.flush()
                    event = parser.feed(line)
                    if event and dashboard:
                        dashboard.update("_integrator", event)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return False

    # Verify no conflict markers remain in porcelain status
    status = await _run_git("status", "--porcelain", cwd=worktree_path, check=False)
    still_conflicted = any(line.startswith("UU") or line.startswith("AA") for line in status.stdout.splitlines())

    if still_conflicted:
        return False

    # Verify no conflict markers remain in file contents
    # Check for standard and diff3-style conflict markers
    grep_result = await _run_git(
        "grep", "-rl", "-E", r"^(<<<<<<<|=======|>>>>>>>|\|\|\|\|\|\|\|)",
        cwd=worktree_path,
        check=False,
    )
    if grep_result.returncode == 0:
        # Conflict markers still present in files
        if dashboard:
            event = Event(
                ts=time.time(),
                kind="error",
                summary=f"conflict markers remain in: {grep_result.stdout.strip()[:100]}",
                raw={},
            )
            dashboard.update("_integrator", event)
        return False

    # Complete the cherry-pick
    env_continue = os.environ.copy()
    env_continue["GIT_EDITOR"] = "true"
    await _run_git("add", "-A", cwd=worktree_path)
    await _run_git("cherry-pick", "--continue", cwd=worktree_path, env=env_continue)
    return True


class _GitResult:
    """Simple container for git command output."""
    __slots__ = ("returncode", "stdout", "stderr")

    def __init__(self, returncode: int, stdout: str, stderr: str):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


async def _run_git(
    *args: str,
    cwd: Path,
    env: dict | None = None,
    check: bool = True,
) -> _GitResult:
    """Run a git command and return stdout/stderr/returncode.

    If check=True (default), raises RuntimeError on non-zero exit code.
    """
    if env is None:
        env = os.environ.copy()
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        cwd=str(cwd),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await proc.communicate()
    result = _GitResult(
        returncode=proc.returncode or 0,
        stdout=stdout_bytes.decode("utf-8", errors="replace"),
        stderr=stderr_bytes.decode("utf-8", errors="replace"),
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed (exit {result.returncode}): {result.stderr}"
        )
    return result
