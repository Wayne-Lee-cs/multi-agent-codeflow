"""Run and resume commands — dispatch tasks, integrate, produce summary."""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .base import (
    _fmt_elapsed,
    _get_repo_root,
    _get_runs_dir,
    _preflight_check,
    _prompt_clean_memory,
)


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


def _execute_run(
    all_tasks: list,
    dispatch_tasks: list,
    run_id: str,
    run_dir: Path,
    base_sha: str,
    repo_root: Path,
    args: argparse.Namespace,
    merge_results: Callable | None = None,
    retry_hint: str | None = None,
    conventions: str = "",
) -> None:
    """Shared run logic: dispatch -> integrate -> summary."""
    from cagent.agent import AgentResult
    from cagent.dispatcher import run
    from cagent.integrator import integrate
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
        printer_task = asyncio.create_task(printer.run())
        try:
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
            )

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

            all_memories = memory.read_all()
            if all_memories:
                summary_parts = [
                    f"## Task {tid}\n{content}"
                    for tid, content in all_memories.items()
                ]
                memory.write_shared(
                    f"# Shared Context — Run {run_id}\n\n" + "\n\n".join(summary_parts)
                )

            integration_sha = None
            if done_count > 0:
                printer.print_integration("starting cherry-pick integration...")
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
                    )
                    printer.print_integration(
                        f"done — branch cagent/{run_id}/integration  tip {integration_sha[:12]}"
                    )
                except Exception as e:
                    printer.print_integration(f"FAILED: {e}")
                    print(f"  Worktree preserved for manual inspection.")
                    integration_sha = None

            return all_results, integration_sha

        finally:
            dashboard.flush()
            dashboard.set_event_handler(None)
            printer_task.cancel()
            try:
                await printer_task
            except asyncio.CancelledError:
                pass

    try:
        results, integration_sha = asyncio.run(_run_all())
    except KeyboardInterrupt:
        elapsed = _fmt_elapsed(time.time() - run_start)
        print(f"\n\nInterrupted after {elapsed}.")

        from cagent.tasks import dump_state

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


def _cmd_run(args: argparse.Namespace) -> None:
    """Execute the full run workflow: dispatch -> integrate -> summary."""
    if args.api_key:
        import os
        os.environ["ANTHROPIC_API_KEY"] = args.api_key

    _preflight_check(check_auth=True)

    from cagent.tasks import dump_state, parse_tasks_file
    from cagent.worktree import current_head

    repo_root = _get_repo_root()

    if args.resume:
        _cmd_resume(args, repo_root)
        return

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

    run_id = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
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

    def _merge_resume_results(all_tasks: list, dispatch_results: list) -> list:
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
    tasks: list,
    results: list,
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


def _clean_worktrees(repo_root: Path, run_dir: Path, tasks: list, results: list) -> None:
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
