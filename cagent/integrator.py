"""Integrator — cherry-pick task commits into a unified integration branch."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from cagent.agent import _resolve_claude
from cagent.git_utils import GitResult
from cagent.memory import RunMemory
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
    memory: RunMemory | None = None,
    post_integrate_cmd: str | None = None,
    strategy: str = "cherry-pick",
    api_key: str | None = None,
) -> str:
    """Integrate all done task commits into an integration branch.

    Supported strategies:
      - cherry-pick: cherry-pick each commit individually (default)
      - merge: merge each task branch into integration branch
      - rebase: rebase task branches onto integration branch

    Returns the integration branch tip SHA.
    """
    run_id = run_dir.name
    integration_branch = f"cagent/{run_id}/integration"
    worktree_path = repo_root / ".cagent" / "worktrees" / run_id / "_integration"

    # Create integration worktree
    from cagent.worktree import create_worktree
    create_worktree(repo_root, worktree_path, integration_branch, base_sha)

    # NOTE: do NOT call prepare_sandbox here — task commits already contain
    # .claude/ files from their own sandbox, and injecting before integration
    # would cause conflicts. The sandbox is injected later in _resolve_conflicts
    # when the integrator agent actually needs it.

    done_tasks = [t for t in tasks if t.status == "done" and t.commit_sha]
    if not done_tasks:
        return base_sha

    # Select integration strategy
    valid_strategies = {"cherry-pick", "merge", "rebase"}
    if strategy not in valid_strategies:
        raise ValueError(f"Unknown strategy: {strategy!r}. Must be one of {valid_strategies}")

    if strategy == "merge":
        integrated, failed = await _merge_strategy(
            tasks=done_tasks,
            worktree_path=worktree_path,
            run_dir=run_dir,
            repo_root=repo_root,
            integration_branch=integration_branch,
            run_id=run_id,
            integrator_model_override=integrator_model_override,
            timeout=timeout,
            dashboard=dashboard,
            memory=memory,
            api_key=api_key,
        )
    elif strategy == "rebase":
        integrated, failed = await _rebase_strategy(
            tasks=done_tasks,
            worktree_path=worktree_path,
            run_dir=run_dir,
            repo_root=repo_root,
            integration_branch=integration_branch,
            integrator_model_override=integrator_model_override,
            timeout=timeout,
            dashboard=dashboard,
            memory=memory,
            api_key=api_key,
        )
    else:
        # Default: cherry-pick
        integrated, failed = await _cherry_pick_strategy(
            tasks=done_tasks,
            worktree_path=worktree_path,
            run_dir=run_dir,
            repo_root=repo_root,
            integrator_model_override=integrator_model_override,
            timeout=timeout,
            dashboard=dashboard,
            memory=memory,
            api_key=api_key,
        )

    if failed and not integrated:
        raise RuntimeError(
            f"All {len(failed)} {strategy} integration attempts failed. "
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

    # Post-integration validation: run user command, repair if it fails (max 2 rounds)
    if post_integrate_cmd and integrated:
        validation_ok = await _post_integrate_validate(
            cmd_str=post_integrate_cmd,
            worktree_path=worktree_path,
            run_dir=run_dir,
            integrator_model_override=integrator_model_override,
            timeout=timeout,
            dashboard=dashboard,
            api_key=api_key,
        )
        if not validation_ok and dashboard:
            event = Event(
                ts=time.time(),
                kind="error",
                summary="post-integrate-cmd failed after 2 repair rounds — integration marked partial",
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
        result = await _run_git("commit", "-m", commit_msg, cwd=worktree_path, check=False)
        if result.returncode != 0:
            # Rollback: reset to base to leave worktree in a clean state
            await _run_git("reset", "--hard", base_sha, cwd=worktree_path, check=False)

    # Get final SHA
    result = await _run_git("rev-parse", "HEAD", cwd=worktree_path)
    return result.stdout.strip()


def _validate_cmd_str(cmd_str: str) -> bool:
    """Validate that a command string does not contain control characters.

    This function validates trusted input from CLI arguments (--post-integrate-cmd).
    The allowed character set includes shell metacharacters (|, &, ;, $, etc.) by
    design, since the caller intentionally passes shell commands. This only rejects
    control characters and null bytes that could cause unexpected behavior in
    subprocess invocation.
    """
    import re
    # Only match space (0x20), not other whitespace like \n, \r, \t
    pattern = r'^[\w .\-\/\\:=+,@~()\[\]{}|&;!?\*#$%^\'"<>]+$'
    return bool(re.match(pattern, cmd_str))


async def _run_shell_cmd(
    cmd_str: str,
    cwd: Path,
    timeout: float = 300,
) -> tuple[int, str]:
    """Run a shell command string and return (returncode, combined output)."""
    import sys as _sys

    if not _validate_cmd_str(cmd_str):
        return 1, f"Command rejected: contains disallowed characters. Only alphanumeric, spaces, and common shell characters are allowed."

    if _sys.platform == "win32":
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

        # Stage + commit the repair
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
    integrator_model_override: str | None,
    timeout: int,
    dashboard: Dashboard | None,
    memory: RunMemory | None = None,
    api_key: str | None = None,
) -> bool:
    """Cherry-pick a single task commit. Returns True on success."""
    if not task.commit_sha:
        raise RuntimeError(f"task {task.id} has no commit_sha")

    # Restore .claude/ and .gitignore to HEAD before cherry-pick to avoid
    # false conflicts from sandbox artifacts that were removed from task commits.
    # NOTE: these must run sequentially — parallel checkout on the same worktree
    # would contend on .git/index.lock.
    await _run_git("checkout", "HEAD", "--", ".claude/", cwd=worktree_path, check=False)
    await _run_git("checkout", "HEAD", "--", ".gitignore", cwd=worktree_path, check=False)

    # Try cherry-pick using _run_git which has timeout=60 + kill
    result = await _run_git("cherry-pick", task.commit_sha, cwd=worktree_path, check=False)
    if result.returncode == 0:
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
        memory=memory,
        api_key=api_key,
    )


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
    # Get conflict file list using porcelain format
    status = await _run_git("status", "--porcelain", cwd=worktree_path, check=False)
    conflict_files = []
    for line in status.stdout.splitlines():
        if len(line) >= 3 and ("U" in line[:2] or line[:2] in ("DD", "AA")):
            raw = line[3:].strip()
            if " -> " in raw:
                raw = raw.split(" -> ", 1)[1]
            conflict_files.append(raw)

    if not conflict_files:
        return False

    # Build integrator prompt — reference tasks already integrated with memory
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

    operation = {"cherry-pick": "cherry-pick", "merge": "merge", "rebase": "rebase"}[completion_mode]
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

    # Verify no conflict markers remain in file contents.
    grep_result = await _run_git(
        "grep", "-rl", "-E", r"^(<{7}|={7}|\|{7}|>{7})",
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

    # Clean sandbox artifacts before staging
    claude_dir = worktree_path / ".claude"
    if claude_dir.exists():
        try:
            shutil.rmtree(claude_dir)
        except OSError:
            pass

    env_continue = {**os.environ, "GIT_EDITOR": "true"}
    try:
        await _run_git("add", "-A", cwd=worktree_path)
    except RuntimeError:
        await _abort_operation(completion_mode, worktree_path)
        return False

    # Complete the operation based on completion_mode
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
            return False
    elif completion_mode == "rebase":
        try:
            await _run_git("rebase", "--continue", cwd=worktree_path, env=env_continue)
        except RuntimeError:
            await _run_git("rebase", "--abort", cwd=worktree_path, check=False)
            return False

    # Append integrator memory
    if memory:
        memory.append("_integrator", (
            f"Resolved conflict for task {task.id} ({task.prompt[:60]})\n"
            f"Conflicting files: {', '.join(conflict_files)}\n"
            f"Preserved intent of both sides."
        ))

    # Update task state
    result = await _run_git("rev-parse", "HEAD", cwd=worktree_path, check=False)
    if result.returncode != 0:
        await _abort_operation(completion_mode, worktree_path)
        return False
    task.commit_sha = result.stdout.strip()
    task.status = "done"
    if dashboard:
        dashboard.set_task_status(task.id, "done", commit_sha=task.commit_sha)

    return True


async def _abort_operation(mode: str, worktree_path: Path) -> None:
    """Abort the current git operation based on mode."""
    if mode == "cherry-pick":
        await _run_git("cherry-pick", "--abort", cwd=worktree_path, check=False)
    elif mode == "merge":
        await _run_git("merge", "--abort", cwd=worktree_path, check=False)
    elif mode == "rebase":
        await _run_git("rebase", "--abort", cwd=worktree_path, check=False)


async def _run_git(
    *args: str,
    cwd: Path,
    env: dict[str, str] | None = None,
    check: bool = True,
    timeout: float = 60,
) -> GitResult:
    """Run a git command and return a GitResult.

    Delegates to cagent.git_utils.run_git_async.
    """
    from cagent.git_utils import run_git_async

    return await run_git_async(
        *args, cwd=cwd, env=env, check=check, timeout=timeout
    )


async def _cherry_pick_strategy(
    tasks: list[Task],
    worktree_path: Path,
    run_dir: Path,
    repo_root: Path,
    integrator_model_override: str | None,
    timeout: int,
    dashboard: Dashboard | None,
    memory: RunMemory | None,
    api_key: str | None = None,
) -> tuple[list[Task], list[Task]]:
    """Cherry-pick strategy: cherry-pick each task commit individually."""
    integrated: list[Task] = []
    failed: list[Task] = []
    for task in tasks:
        try:
            success = await _cherry_pick_one(
                task=task,
                integrated_tasks=integrated,
                worktree_path=worktree_path,
                run_dir=run_dir,
                integrator_model_override=integrator_model_override,
                timeout=timeout,
                dashboard=dashboard,
                memory=memory,
                api_key=api_key,
            )
        except Exception as e:
            success = False
            if dashboard:
                event = Event(
                    ts=time.time(),
                    kind="error",
                    summary=f"cherry-pick task {task.id} exception: {e}",
                    raw={},
                )
                dashboard.update("_integrator", event)
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
    return integrated, failed


async def _merge_strategy(
    tasks: list[Task],
    worktree_path: Path,
    run_dir: Path,
    repo_root: Path,
    integration_branch: str,
    run_id: str,
    integrator_model_override: str | None,
    timeout: int,
    dashboard: Dashboard | None,
    memory: RunMemory | None,
    api_key: str | None = None,
) -> tuple[list[Task], list[Task]]:
    """Merge strategy: merge each task branch into integration branch."""
    integrated = []
    failed = []
    temp_branches = []

    for task in tasks:
        if not task.commit_sha:
            failed.append(task)
            continue

        if dashboard:
            event = Event(
                ts=time.time(),
                kind="text",
                summary=f"merging task {task.id}...",
                raw={},
            )
            dashboard.update("_integrator", event)

        # Create a temporary branch for the task (unique per run)
        task_branch = f"cagent/{run_id}/task-{task.id}"
        temp_branches.append(task_branch)
        try:
            await _run_git("branch", "-f", task_branch, task.commit_sha, cwd=worktree_path, check=False)

            # Try merge
            result = await _run_git("merge", "--no-ff", task_branch, cwd=worktree_path, check=False)

            if result.returncode == 0:
                integrated.append(task)
                if dashboard:
                    event = Event(
                        ts=time.time(),
                        kind="text",
                        summary=f"task {task.id} merged successfully",
                        raw={},
                    )
                    dashboard.update("_integrator", event)
            else:
                # Check for conflicts
                status = await _run_git("status", "--porcelain", cwd=worktree_path, check=False)
                if _has_conflict_markers(status.stdout):
                    # Resolve conflicts with integrator agent
                    success = await _resolve_conflicts(
                        task=task,
                        integrated_tasks=integrated,
                        worktree_path=worktree_path,
                        run_dir=run_dir,
                        integrator_model_override=integrator_model_override,
                        timeout=timeout,
                        dashboard=dashboard,
                        memory=memory,
                        completion_mode="merge",
                        api_key=api_key,
                    )
                    if success:
                        integrated.append(task)
                    else:
                        failed.append(task)
                else:
                    failed.append(task)
                    await _run_git("merge", "--abort", cwd=worktree_path, check=False)
        except Exception as e:
            failed.append(task)
            if dashboard:
                event = Event(
                    ts=time.time(),
                    kind="error",
                    summary=f"merge task {task.id} exception: {e}",
                    raw={},
                )
                dashboard.update("_integrator", event)

    # Clean up temporary branches
    for branch in temp_branches:
        await _run_git("branch", "-D", branch, cwd=worktree_path, check=False)

    return integrated, failed


async def _rebase_strategy(
    tasks: list[Task],
    worktree_path: Path,
    run_dir: Path,
    repo_root: Path,
    integration_branch: str,
    integrator_model_override: str | None,
    timeout: int,
    dashboard: Dashboard | None,
    memory: RunMemory | None,
    api_key: str | None = None,
) -> tuple[list[Task], list[Task]]:
    """Rebase strategy: replay task commits onto integration branch.

    Note: internally uses cherry-pick (not git rebase), which is equivalent
    to a "replay" strategy. For single-commit branches this behaves identically
    to rebase; for multi-commit branches, each commit is replayed independently.
    """
    integrated = []
    failed = []

    # Collect all task commits
    task_commits = [(task, task.commit_sha) for task in tasks if task.commit_sha]
    if not task_commits:
        return [], list(tasks)

    # Create a temporary branch (unique per run)
    run_id = integration_branch.split("/")[1]
    temp_branch = f"cagent/{run_id}/temp-rebase"
    try:
        # Create temp branch from current HEAD
        await _run_git("checkout", "-b", temp_branch, cwd=worktree_path, check=True)

        for task, sha in task_commits:
            if dashboard:
                event = Event(
                    ts=time.time(),
                    kind="text",
                    summary=f"rebasing task {task.id}...",
                    raw={},
                )
                dashboard.update("_integrator", event)

            # Try cherry-pick during rebase
            result = await _run_git("cherry-pick", sha, cwd=worktree_path, check=False)

            if result.returncode == 0:
                integrated.append(task)
            else:
                # Check for conflicts
                status = await _run_git("status", "--porcelain", cwd=worktree_path, check=False)
                if _has_conflict_markers(status.stdout):
                    # Resolve conflicts with integrator agent
                    success = await _resolve_conflicts(
                        task=task,
                        integrated_tasks=integrated,
                        worktree_path=worktree_path,
                        run_dir=run_dir,
                        integrator_model_override=integrator_model_override,
                        timeout=timeout,
                        dashboard=dashboard,
                        memory=memory,
                        completion_mode="rebase",
                        api_key=api_key,
                    )
                    if success:
                        integrated.append(task)
                    else:
                        failed.append(task)
                else:
                    failed.append(task)
                    await _run_git("cherry-pick", "--abort", cwd=worktree_path, check=False)

        # Update integration branch to point at temp branch HEAD
        result = await _run_git("rev-parse", "HEAD", cwd=worktree_path, check=False)
        temp_sha = result.stdout.strip()
        await _run_git("branch", "-f", integration_branch, temp_sha, cwd=worktree_path, check=False)
        await _run_git("checkout", integration_branch, cwd=worktree_path, check=False)

    except Exception as e:
        if dashboard:
            event = Event(
                ts=time.time(),
                kind="error",
                summary=f"rebase strategy exception: {e}",
                raw={},
            )
            dashboard.update("_integrator", event)
        failed = [t for t in tasks if t not in integrated]
    finally:
        # Clean up temp branch
        await _run_git("branch", "-D", temp_branch, cwd=worktree_path, check=False)

    return integrated, failed


