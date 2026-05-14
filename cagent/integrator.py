"""Integrator — cherry-pick task commits into a unified integration branch."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from cagent.agent import _resolve_claude
from cagent.progress import Dashboard, Event, EventParser
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

    # NOTE: do NOT call prepare_sandbox here — task commits already contain
    # .claude/ files from their own sandbox, and injecting before cherry-pick
    # would cause conflicts. The sandbox is injected later in _resolve_conflicts
    # when the integrator agent actually needs it.

    done_tasks = [t for t in tasks if t.status == "done" and t.commit_sha]
    if not done_tasks:
        return base_sha

    integrated = []
    failed = []
    for task in done_tasks:
        try:
            success = await _cherry_pick_one(
                task=task,
                integrated_tasks=integrated,
                worktree_path=worktree_path,
                run_dir=run_dir,
                repo_root=repo_root,
                integrator_model_override=integrator_model_override,
                timeout=timeout,
                dashboard=dashboard,
            )
        except Exception:
            success = False
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
        # Remove sandbox files from index (they may have been committed during conflict resolution)
        await _run_git("rm", "--cached", "-r", ".claude/", cwd=worktree_path, check=False)
        summary_parts = [f"task {t.id}: {t.prompt.split(chr(10))[0][:50]}" for t in integrated]
        commit_msg = "integrate:\n" + "\n".join(f"- {s}" for s in summary_parts)
        await _run_git("commit", "-m", commit_msg, cwd=worktree_path)

    # Get final SHA
    result = await _run_git("rev-parse", "HEAD", cwd=worktree_path)
    return result.stdout.strip()


def _has_conflict_markers(status_output: str) -> bool:
    """Check if git porcelain status contains any conflict markers."""
    for line in status_output.splitlines():
        if len(line) < 2:
            continue
        xy = line[:2]
        # UU=both-modified, AA=both-added, DD=both-deleted
        # AU=added-by-us, UA=added-by-them, UD=modified-us-deleted-them, DU=deleted-us-modified-them
        if "U" in xy or xy in ("DD", "AA"):
            return True
    return False


async def _cherry_pick_one(
    task: Task,
    integrated_tasks: list[Task],
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

    # Restore .claude/ and .gitignore to HEAD before cherry-pick to avoid
    # false conflicts from sandbox artifacts that were removed from task commits.
    await _run_git("checkout", "HEAD", "--", ".claude/", cwd=worktree_path, check=False)
    await _run_git("checkout", "HEAD", "--", ".gitignore", cwd=worktree_path, check=False)

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

    if not _has_conflict_markers(status.stdout):
        # Cherry-pick failed for non-conflict reason
        await _run_git("cherry-pick", "--abort", cwd=worktree_path, check=False)
        return False

    # Resolve conflicts with integrator agent
    return await _resolve_conflicts(
        task=task,
        integrated_tasks=integrated_tasks,
        worktree_path=worktree_path,
        run_dir=run_dir,
        integrator_model_override=integrator_model_override,
        timeout=timeout,
        dashboard=dashboard,
    )


async def _resolve_conflicts(
    task: Task,
    integrated_tasks: list[Task],
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
        if len(line) >= 3 and ("U" in line[:2] or line[:2] in ("DD", "AA")):
            conflict_files.append(line[3:].strip())

    # Build integrator prompt — only reference tasks already cherry-picked
    merged_summaries = "\n".join(
        f"  - task {t.id}: {t.prompt.split(chr(10))[0][:80]}"
        for t in integrated_tasks if t != task
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
        f"have no <<<<<<< ======= >>>>>>> markers."
    )

    # NOTE: Do NOT inject safety sandbox for integrator — the integrator agent
    # needs full Bash access to run git add/cherry-pick --continue. The integrator
    # is orchestrated by cagent and only runs specific git commands after conflict
    # resolution, so the sandbox would block legitimate operations.

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
                    for event in parser.feed(line):
                        if dashboard:
                            dashboard.update("_integrator", event)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        await _run_git("cherry-pick", "--abort", cwd=worktree_path, check=False)
        return False

    # Verify no conflict markers remain in file contents.
    # Note: git status may still show UU (unmerged) because the integrator
    # edited the file without staging it. We check actual file content instead.
    # Use git grep on tracked+modified source files only.
    grep_result = await _run_git(
        "grep", "-rl", "-E", r"^(<{7}|={7}|\|{7}|>{7})",
        "--", "*.py", "*.md", "*.txt", "*.json", "*.js", "*.ts", "*.html", "*.css",
        "*.yaml", "*.yml", "*.toml", "*.cfg", "*.ini", "*.sh", "*.cmd", "*.bat",
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
        await _run_git("cherry-pick", "--abort", cwd=worktree_path, check=False)
        return False

    # Complete the cherry-pick
    env_continue = os.environ.copy()
    env_continue["GIT_EDITOR"] = "true"
    try:
        await _run_git("add", "-A", cwd=worktree_path)
    except RuntimeError:
        await _run_git("cherry-pick", "--abort", cwd=worktree_path, check=False)
        return False
    try:
        await _run_git("cherry-pick", "--continue", cwd=worktree_path, env=env_continue)
    except RuntimeError:
        await _run_git("cherry-pick", "--abort", cwd=worktree_path, check=False)
        return False
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
