"""Run and resume commands — dispatch tasks, integrate, produce summary."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from cagent.progress import Dashboard

from .base import (
    _fmt_elapsed,
    _get_repo_root,
    _get_runs_dir,
    _preflight_check,
    _prompt_clean_memory,
)


@contextlib.contextmanager
def _run_lock(repo_root: Path, force: bool = False):
    """Acquire a per-repo run lock to prevent concurrent cagent runs.

    Uses OS-level file locking: msvcrt on Windows, fcntl on Unix.
    The lock is held until the context manager exits.
    """
    lock_dir = repo_root / ".cagent"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / "run.lock"

    if force:
        yield
        return

    lock_fd = None
    try:
        lock_fd = open(lock_path, "w")
        if sys.platform == "win32":
            import msvcrt
            try:
                msvcrt.locking(lock_fd.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                lock_fd.close()
                print(
                    "Error: Another cagent run is active in this repository.\n"
                    "  Use --force to override (only if you're sure no other run is active).",
                    file=sys.stderr,
                )
                sys.exit(1)
        else:
            import fcntl
            try:
                fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                lock_fd.close()
                print(
                    "Error: Another cagent run is active in this repository.\n"
                    "  Use --force to override (only if you're sure no other run is active).",
                    file=sys.stderr,
                )
                sys.exit(1)
        lock_fd.write(str(os.getpid()))
        lock_fd.flush()
        yield
    finally:
        if lock_fd is not None:
            try:
                try:
                    if sys.platform == "win32":
                        import msvcrt
                        try:
                            msvcrt.locking(lock_fd.fileno(), msvcrt.LK_UNLCK, 1)
                        except OSError:
                            pass
                    else:
                        import fcntl
                        try:
                            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
                        except OSError:
                            pass
                finally:
                    lock_fd.close()
            except (OSError, ValueError):
                pass
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass


def _print_task_timing(dashboard: "Dashboard") -> None:
    """Print per-task timing stats after a run completes."""
    tasks = dashboard.tasks
    if not tasks:
        return
    print()
    print("Task timing:")
    for tid in sorted(tasks.keys()):
        tp = tasks[tid]
        elapsed_str = ""
        if tp.started_at and tp.ended_at:
            secs = int(tp.ended_at - tp.started_at)
            elapsed_str = _fmt_elapsed(secs)
        elif tp.started_at:
            elapsed_str = "still running"
        status = tp.status
        sha = f" {tp.commit_sha[:7]}" if tp.commit_sha else ""
        tools = f" ({tp.tool_count} tools)" if tp.tool_count else ""
        print(f"  [{tid}] {status:<6} {elapsed_str:>8}{tools}{sha}")


async def _dispatch_phase(
    dispatch_tasks: list[Any],
    all_tasks: list[Any],
    args: argparse.Namespace,
    run_dir: Path,
    base_sha: str,
    repo_root: Path,
    dashboard: Any,
    memory: Any,
    conventions: str,
    api_key: str | None,
    merge_results: Callable[..., Any] | None,
) -> list[Any]:
    """Run the dispatcher and return merged results."""
    from cagent.dispatcher import run

    results = await run(
        tasks=dispatch_tasks,
        concurrency=args.jobs,
        run_dir=run_dir,
        base_sha=base_sha,
        repo_root=repo_root,
        worker_model_override=args.worker_model,
        timeout=args.timeout,
        dashboard=dashboard,
        memory=memory,
        conventions=conventions,
        retries=args.retries,
        max_turns=getattr(args, "max_turns", None),
        max_tokens=getattr(args, "max_tokens", None),
        api_key=api_key,
    )

    all_results: list[Any]
    if merge_results:
        all_results = merge_results(all_tasks, results)
    else:
        all_results = results

    done_count = sum(1 for r in all_results if r.status == "done")
    failed_count = sum(1 for r in all_results if r.status == "failed")
    noop_count = sum(1 for r in all_results if r.status == "noop")

    print()
    print(f"Dispatcher: {done_count} done, {failed_count} failed, {noop_count} noop")
    _print_task_timing(dashboard)

    return all_results


async def _integrate_phase(
    all_tasks: list[Any],
    all_results: list[Any],
    run_id: str,
    run_dir: Path,
    base_sha: str,
    repo_root: Path,
    args: argparse.Namespace,
    dashboard: Any,
    memory: Any,
    api_key: str | None,
) -> str | None:
    """Write shared memory and run integration. Returns integration SHA or None."""
    from cagent.integrator import integrate

    all_memories = memory.read_all()
    if all_memories:
        summary_parts = [
            f"## Task {tid}\n{content}"
            for tid, content in all_memories.items()
        ]
        memory.write_shared(
            f"# Shared Context — Run {run_id}\n\n" + "\n\n".join(summary_parts)
        )

    done_count = sum(1 for r in all_results if r.status == "done")
    if done_count == 0:
        return None

    strategy = getattr(args, "strategy", "cherry-pick")
    # printer is attached to dashboard event handler
    print(f"  integration: starting {strategy}...")
    try:
        integration_sha = await integrate(
            tasks=all_tasks,
            run_dir=run_dir,
            base_sha=base_sha,
            repo_root=repo_root,
            squash=args.squash,
            integrator_model_override=args.integrator_model,
            timeout=args.timeout,
            dashboard=dashboard,
            memory=memory,
            post_integrate_cmd=getattr(args, "post_integrate_cmd", None),
            strategy=strategy,
            api_key=api_key,
        )
        print(f"  integration: done — branch cagent/{run_id}/integration  tip {integration_sha[:12]}")
        return integration_sha
    except Exception as e:
        print(f"  integration: FAILED: {e}")
        print(f"  Worktree preserved for manual inspection.")
        return None


def _summary_phase(
    all_tasks: list[Any],
    results: list[Any],
    run_id: str,
    run_dir: Path,
    base_sha: str,
    repo_root: Path,
    integration_sha: str | None,
    elapsed: str,
    args: argparse.Namespace,
) -> None:
    """Write summary, clean worktrees, and print final status."""
    _write_summary(run_dir, all_tasks, results, base_sha, integration_sha, run_id, elapsed)

    if not args.keep_worktrees:
        _clean_worktrees(repo_root, run_dir, all_tasks, results)

    print()
    if integration_sha:
        print(f"Done! ({elapsed})")
        print(f"  Integration branch: cagent/{run_id}/integration")
        print(f"  To merge:  git merge cagent/{run_id}/integration")
        print(f"  To push:   cagent push cagent/{run_id}/integration")
    else:
        print(f"Run completed in {elapsed} with no successful tasks to integrate.")

    memory_dir = run_dir / "memory"
    if memory_dir.exists() and any(memory_dir.iterdir()):
        print(f"\n  Subagent memory: {memory_dir}")
        _prompt_clean_memory(memory_dir)


def _execute_run(
    all_tasks: list[Any],
    dispatch_tasks: list[Any],
    run_id: str,
    run_dir: Path,
    base_sha: str,
    repo_root: Path,
    args: argparse.Namespace,
    merge_results: Callable[..., Any] | None = None,
    retry_hint: str | None = None,
    conventions: str = "",
    api_key: str | None = None,
) -> None:
    """Shared run logic: dispatch -> integrate -> summary."""
    from cagent.log import LinePrinter
    from cagent.memory import RunMemory
    from cagent.progress import Dashboard
    from cagent.tasks import dump_state

    run_start = time.time()

    dashboard = Dashboard(run_dir)
    memory = RunMemory(run_dir)
    printer = LinePrinter(dashboard, quiet=args.quiet)
    dashboard.set_event_handler(printer.push)

    async def _run_all():
        dashboard.start_async_io()
        printer_task = asyncio.create_task(printer.run())
        try:
            all_results = await _dispatch_phase(
                dispatch_tasks, all_tasks, args, run_dir, base_sha,
                repo_root, dashboard, memory, conventions, api_key, merge_results,
            )

            integration_sha = await _integrate_phase(
                all_tasks, all_results, run_id, run_dir, base_sha,
                repo_root, args, dashboard, memory, api_key,
            )

            return all_results, integration_sha

        finally:
            try:
                await dashboard.flush_async()
            except (asyncio.CancelledError, Exception):
                dashboard.flush()  # Sync fallback on cancellation
            dashboard.set_event_handler(None)
            printer_task.cancel()
            try:
                await printer_task
            except asyncio.CancelledError:
                pass
            await dashboard.stop_async_io()

    try:
        results, integration_sha = asyncio.run(_run_all())
    except KeyboardInterrupt:
        elapsed = _fmt_elapsed(time.time() - run_start)
        print(f"\n\nInterrupted after {elapsed}.")

        # Terminate worker subprocesses via PID files
        pids_dir = run_dir / "pids"
        if pids_dir.exists():
            from .base import _terminate_pid
            for pid_file in pids_dir.glob("*.pid"):
                try:
                    pid = int(pid_file.read_text(encoding="utf-8").strip())
                    _terminate_pid(pid)
                except (ValueError, OSError, ProcessLookupError):
                    pass

        # Persist current task state
        dump_state(run_dir, all_tasks)
        done = sum(1 for t in all_tasks if t.status == "done")
        failed = sum(1 for t in all_tasks if t.status == "failed")
        running = sum(1 for t in all_tasks if t.status == "running")
        print(f"  {done} done, {failed} failed, {running} interrupted")
        print(f"  State saved to {run_dir}")
        print(f"  To clean up worktrees:  cagent clean {run_dir.name}")
        if retry_hint:
            print(f"\n  {retry_hint}")
        sys.exit(130)

    elapsed = _fmt_elapsed(time.time() - run_start)
    _summary_phase(
        all_tasks, results, run_id, run_dir, base_sha,
        repo_root, integration_sha, elapsed, args,
    )


def _cmd_run(args: argparse.Namespace) -> None:
    """Execute the full run workflow: dispatch -> integrate -> summary."""
    repo_root = _get_repo_root()

    from cagent.config import apply_config, load_config
    apply_config(args, load_config(repo_root))

    _preflight_check(
        check_auth=True,
        repo_root=repo_root,
        force_auth=getattr(args, "api_key", None) is not None,
    )

    with _run_lock(repo_root, force=getattr(args, "force", False)):
        if args.resume:
            _cmd_resume(args, repo_root)
            return
        _cmd_run_inner(args, repo_root)


def _cmd_run_inner(args: argparse.Namespace, repo_root: Path) -> None:
    """Inner run logic, called while holding the run lock."""
    from cagent.tasks import dump_state, parse_tasks_file
    from cagent.worktree import current_head

    if args.base:
        try:
            result = subprocess.run(
                ["git", "rev-parse", args.base],
                cwd=repo_root, capture_output=True, text=True, check=True,
            )
            base_sha = result.stdout.strip()
        except subprocess.CalledProcessError:
            print(f"Error: invalid base '{args.base}' — not a valid branch or SHA.", file=sys.stderr)
            sys.exit(1)
    else:
        base_sha = current_head(repo_root)

    run_id = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H-%M-%S-%f")
    run_dir = _get_runs_dir(repo_root) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    conventions = ""
    try:
        if args.tasks_file.endswith(".md"):
            from cagent.tasks import parse_tasks_md
            tasks, conventions = parse_tasks_md(args.tasks_file, run_id)
        else:
            tasks = parse_tasks_file(args.tasks_file, run_id)
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    for t in tasks:
        t.log_path = run_dir / "logs" / f"task-{t.id}.log"

    if args.dry_run:
        print(f"Dry run — planned execution:")
        print(f"  base:     {base_sha[:12]}")
        print(f"  tasks:    {len(tasks)}")
        print(f"  jobs:     {args.jobs}")
        print(f"  timeout:  {args.timeout}s")
        print(f"  squash:   {'yes' if args.squash else 'no'}")
        print(f"  strategy: {args.strategy}")
        print(f"  model:    {args.worker_model or '(inherit from Claude Code)'}")
        print()
        print("Tasks:")
        for t in tasks:
            prompt_preview = t.prompt.split("\n")[0][:60]
            print(f"  [{t.id}] {prompt_preview}")
        print()
        print("Run with: python -m cagent run", args.tasks_file)
        return

    dump_state(run_dir, tasks)

    (run_dir / "base_sha").write_text(base_sha, encoding="utf-8")

    if conventions:
        (run_dir / "conventions.txt").write_text(conventions, encoding="utf-8")

    if args.max_tokens is not None:
        (run_dir / "budget.json").write_text(
            json.dumps({"max_tokens": args.max_tokens}), encoding="utf-8"
        )

    print(f"cagent run {run_id}")
    print(f"  base:     {base_sha[:12]}")
    print(f"  tasks:    {len(tasks)}")
    print(f"  jobs:     {args.jobs}")
    print(f"  timeout:  {args.timeout}s")
    if args.max_turns is not None:
        print(f"  max-turns: {args.max_turns}")
    if args.max_tokens is not None:
        print(f"  budget:   {args.max_tokens:,} tokens")
    print()

    _execute_run(
        all_tasks=tasks,
        dispatch_tasks=tasks,
        run_id=run_id,
        run_dir=run_dir,
        base_sha=base_sha,
        repo_root=repo_root,
        args=args,
        retry_hint=f"To retry: python -m cagent run {args.tasks_file}",
        conventions=conventions,
        api_key=getattr(args, "api_key", None),
    )


def _cmd_resume(args: argparse.Namespace, repo_root: Path) -> None:
    """Resume a previous run, skipping already-completed tasks."""
    from cagent.agent import AgentResult
    from cagent.tasks import dump_state, load_state
    from cagent.worktree import current_head

    runs_dir = _get_runs_dir(repo_root)
    run_dir = runs_dir / args.resume
    if not run_dir.exists():
        print(f"Run not found: {args.resume}", file=sys.stderr)
        print(f"Available runs:")
        if runs_dir.exists():
            for d in sorted(runs_dir.iterdir(), reverse=True):
                if d.is_dir():
                    print(f"  {d.name}")
        sys.exit(1)

    try:
        tasks = load_state(run_dir)
    except FileNotFoundError:
        print(f"No tasks.json found in {run_dir}", file=sys.stderr)
        sys.exit(1)

    run_id = run_dir.name

    conventions = ""
    conv_file = run_dir / "conventions.txt"
    if conv_file.exists():
        conventions = conv_file.read_text(encoding="utf-8")

    pending_tasks = [t for t in tasks if t.status not in ("done", "noop")]
    done_tasks = [t for t in tasks if t.status in ("done", "noop")]

    if not pending_tasks:
        print(f"All {len(tasks)} tasks already completed. Nothing to resume.")
        return

    print(f"Resuming run {run_id}")
    print(f"  Already done: {len(done_tasks)}")
    print(f"  To run:       {len(pending_tasks)}")
    print()

    for t in pending_tasks:
        t.status = "pending"
        t.commit_sha = None
        wt_path = repo_root / ".cagent" / "worktrees" / run_id / f"task-{t.id}"
        if wt_path.exists():
            try:
                subprocess.run(
                    ["git", "worktree", "remove", "--force", str(wt_path)],
                    cwd=repo_root, capture_output=True, check=True,
                )
            except subprocess.CalledProcessError:
                pass
        try:
            subprocess.run(
                ["git", "branch", "-D", t.branch],
                cwd=repo_root, capture_output=True,
            )
        except subprocess.CalledProcessError:
            pass
    dump_state(run_dir, tasks)

    base_sha_file = run_dir / "base_sha"
    if base_sha_file.exists():
        base_sha = base_sha_file.read_text(encoding="utf-8").strip()
    else:
        base_sha = current_head(repo_root)
        print(f"Warning: base_sha file not found in {run_dir.name}, falling back to HEAD ({base_sha[:12]})", file=sys.stderr)

    def _merge_resume_results(all_tasks: list[Any], dispatch_results: list[Any]) -> list[Any]:
        result_map = {r.task_id: r for r in dispatch_results}
        merged = []
        for t in all_tasks:
            if t.id in result_map:
                merged.append(result_map[t.id])
            else:
                merged.append(AgentResult(task_id=t.id, status=t.status, commit_sha=t.commit_sha))
        return merged

    _execute_run(
        all_tasks=tasks,
        dispatch_tasks=pending_tasks,
        run_id=run_id,
        run_dir=run_dir,
        base_sha=base_sha,
        repo_root=repo_root,
        args=args,
        merge_results=_merge_resume_results,
        conventions=conventions,
    )


def _write_summary(
    run_dir: Path,
    tasks: list[Any],
    results: list[Any],
    base_sha: str,
    integration_sha: str | None,
    run_id: str,
    elapsed: str = "",
) -> None:
    """Write summary.md for the run."""
    lines = [f"# cagent run {run_id}\n"]
    lines.append(f"Base: `{base_sha[:12]}`\n")
    if integration_sha:
        lines.append(f"Integration: `{integration_sha[:12]}`\n")
    if elapsed:
        lines.append(f"Elapsed: {elapsed}\n")

    done = sum(1 for t in tasks if t.status == "done")
    failed = sum(1 for t in tasks if t.status == "failed")
    noop = sum(1 for t in tasks if t.status == "noop")
    lines.append(f"\n**{done} done, {failed} failed, {noop} skipped**\n")

    total_in = sum(getattr(r, "tokens_in", 0) for r in results)
    total_out = sum(getattr(r, "tokens_out", 0) for r in results)
    # Prefer dashboard cumulative totals (accurate across resume)
    dashboard_path = run_dir / "dashboard.json"
    if dashboard_path.exists():
        try:
            dash_data = json.loads(dashboard_path.read_text(encoding="utf-8"))
            dash_in = sum(v.get("tokens_in", 0) for v in dash_data.values())
            dash_out = sum(v.get("tokens_out", 0) for v in dash_data.values())
            if dash_in + dash_out > total_in + total_out:
                total_in, total_out = dash_in, dash_out
        except (ValueError, OSError, AttributeError):
            pass
    if total_in or total_out:
        budget_path = run_dir / "budget.json"
        budget_note = ""
        if budget_path.exists():
            try:
                budget_data = json.loads(budget_path.read_text(encoding="utf-8"))
                max_tok = budget_data.get("max_tokens")
                if max_tok:
                    pct = (total_in + total_out) * 100 // max_tok
                    budget_note = f" (budget: {max_tok:,}, used {pct}%)"
            except (ValueError, OSError):
                pass
        lines.append(f"Tokens: {total_in:,} in, {total_out:,} out{budget_note}\n")

    lines.append("\n## Tasks\n")
    for t in tasks:
        status_icon = {"done": "OK", "failed": "FAIL", "noop": "SKIP"}.get(t.status, "?")
        sha = f" `{t.commit_sha[:7]}`" if t.commit_sha else ""
        lines.append(f"- [{status_icon}] task {t.id}: {t.prompt[:60]}{sha}\n")

    if integration_sha:
        lines.append("\n## Next Steps\n")
        lines.append(f"```\n")
        lines.append(f"git merge cagent/{run_id}/integration\n")
        lines.append(f"cagent push cagent/{run_id}/integration\n")
        lines.append(f"```\n")

    (run_dir / "summary.md").write_text("".join(lines), encoding="utf-8")


def _clean_worktrees(repo_root: Path, run_dir: Path, tasks: list[Any], results: list[Any]) -> None:
    """Clean up worktrees based on success/failure status."""
    all_ok = all(r.status in ("done", "noop") for r in results)
    result_map = {r.task_id: r for r in results}

    for task in tasks:
        result = result_map.get(task.id)
        wt_path = repo_root / ".cagent" / "worktrees" / run_dir.name / f"task-{task.id}"
        if wt_path.exists():
            if all_ok or (result and result.status != "failed"):
                try:
                    subprocess.run(
                        ["git", "worktree", "remove", "--force", str(wt_path)],
                        cwd=repo_root, capture_output=True, check=True,
                    )
                except subprocess.CalledProcessError:
                    pass

    if all_ok:
        integration_wt = repo_root / ".cagent" / "worktrees" / run_dir.name / "_integration"
        if integration_wt.exists():
            try:
                subprocess.run(
                    ["git", "worktree", "remove", "--force", str(integration_wt)],
                    cwd=repo_root, capture_output=True, check=True,
                )
            except subprocess.CalledProcessError:
                pass
