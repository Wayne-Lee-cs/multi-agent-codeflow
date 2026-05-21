"""Integrator — cherry-pick task commits into a unified integration branch."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

from cagent.agent import _resolve_claude
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
                memory=memory,
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

    # Post-integration validation: run user command, repair if it fails (max 2 rounds)
    if post_integrate_cmd and integrated:
        validation_ok = await _post_integrate_validate(
            cmd_str=post_integrate_cmd,
            worktree_path=worktree_path,
            run_dir=run_dir,
            integrator_model_override=integrator_model_override,
            timeout=timeout,
            dashboard=dashboard,
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
        await _run_git("commit", "-m", commit_msg, cwd=worktree_path)

    # Get final SHA
    result = await _run_git("rev-parse", "HEAD", cwd=worktree_path)
    return result.stdout.strip()


async def _run_shell_cmd(
    cmd_str: str,
    cwd: Path,
    timeout: float = 300,
) -> tuple[int, str]:
    """Run a shell command string and return (returncode, combined output)."""
    import sys as _sys

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
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(worktree_path),
        env=None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        stdin=asyncio.subprocess.PIPE,
        creationflags=creation_flags,
    )

    if proc.stdin is None:
        return None
    try:
        proc.stdin.write(prompt.encode("utf-8"))
        await proc.stdin.drain()
    finally:
        proc.stdin.close()
        await proc.stdin.wait_closed()

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
    repo_root: Path,
    integrator_model_override: str | None,
    timeout: int,
    dashboard: Dashboard | None,
    memory: RunMemory | None = None,
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
) -> bool:
    """Use an integrator agent to resolve cherry-pick conflicts."""
    # Get conflict file list using porcelain format
    status = await _run_git("status", "--porcelain", cwd=worktree_path, check=False)
    conflict_files = []
    for line in status.stdout.splitlines():
        if len(line) >= 3 and ("U" in line[:2] or line[:2] in ("DD", "AA")):
            raw = line[3:].strip()
            if " -> " in raw:
                raw = raw.split(" -> ", 1)[1]
            conflict_files.append(raw)

    # Build integrator prompt — reference tasks already cherry-picked with memory
    merged_summaries = ""
    for t in integrated_tasks:
        if t == task:
            continue
        task_memory = memory.read(t.id) if memory else ""
        if task_memory:
            merged_summaries += f"  - task {t.id} ({t.prompt[:50]}):\n    {task_memory[:300]}\n"
        else:
            merged_summaries += f"  - task {t.id}: {t.prompt.split(chr(10))[0][:80]}\n"

    conflict_list = "\n".join(f"  - {f}" for f in conflict_files)

    if merged_summaries:
        context_block = (
            f"The current task (task {task.id}) has conflicts with already-merged tasks:\n"
            f"{merged_summaries}"
        )
    else:
        context_block = (
            f"The current task (task {task.id}) conflicts with the base branch.\n"
            f"There are no previously merged tasks — this is the first cherry-pick."
        )

    prompt = (
        f"You are resolving merge conflicts in a cherry-pick operation.\n\n"
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
            summary=f"cherry-pick task {task.id} → conflict, launching integrator",
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
        await _run_git("cherry-pick", "--abort", cwd=worktree_path, check=False)
        return False

    # Verify no conflict markers remain in file contents.
    # Note: git status may still show UU (unmerged) because the integrator
    # edited the file without staging it. We check actual file content instead.
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
        await _run_git("cherry-pick", "--abort", cwd=worktree_path, check=False)
        return False

    # Complete the cherry-pick
    # Clean sandbox artifacts before staging (mirrors _commit_result behavior)
    claude_dir = worktree_path / ".claude"
    for f in [claude_dir / "settings.local.json", claude_dir / "hooks" / "cagent-guard.py"]:
        if f.exists():
            try:
                f.unlink()
            except OSError:
                pass
    env_continue = {**os.environ, "GIT_EDITOR": "true"}
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

    # Append integrator memory (preserves previous conflict resolutions)
    if memory:
        memory.append("_integrator", (
            f"Resolved conflict for task {task.id} ({task.prompt[:60]})\n"
            f"Conflicting files: {', '.join(conflict_files)}\n"
            f"Preserved intent of both sides."
        ))

    # Update task state so it reflects the successful cherry-pick result.
    # This is critical: subsequent integration runs check task.commit_sha
    # to determine which tasks have been integrated.
    result = await _run_git("rev-parse", "HEAD", cwd=worktree_path, check=False)
    task.commit_sha = result.stdout.strip()
    task.status = "done"

    return True


async def _run_git(
    *args: str,
    cwd: Path,
    env: dict | None = None,
    check: bool = True,
    timeout: float = 60,
):
    """Run a git command and return a GitResult.

    Delegates to cagent.git_utils.run_git_async.
    """
    from cagent.git_utils import run_git_async

    return await run_git_async(
        *args, cwd=cwd, env=env, check=check, timeout=timeout
    )
