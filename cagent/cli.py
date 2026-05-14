"""CLI entry point — argparse subcommands for cagent."""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from cagent.compat import enable_ansi, is_tty, stdin_has_key, read_key


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="cagent",
        description="Concurrent agent workflow dispatcher",
    )
    sub = parser.add_subparsers(dest="command")

    # --- run ---
    run_p = sub.add_parser("run", help="Run tasks from a file concurrently")
    run_p.add_argument("tasks_file", help="Path to tasks file")
    run_p.add_argument("-j", "--jobs", type=int, default=4, help="Concurrency (default: 4)")
    run_p.add_argument("--base", default=None, help="Base branch/SHA (default: HEAD)")
    run_p.add_argument("--squash", action="store_true", help="Squash integration into one commit")
    run_p.add_argument("--keep-worktrees", action="store_true", help="Keep worktrees after run")
    run_p.add_argument("--worker-model", default=None, help="Model override for workers")
    run_p.add_argument("--integrator-model", default=None, help="Model override for integrator")
    run_p.add_argument("--timeout", type=int, default=1800, help="Per-agent timeout in seconds")
    run_p.add_argument("--quiet", action="store_true", help="Only print START/DONE/FAIL events")
    run_p.add_argument("--api-key", default=None, help="Explicit API key for claude -p subprocesses")
    run_p.add_argument("--dry-run", action="store_true", help="Show plan without executing")
    run_p.add_argument("--resume", default=None, help="Resume from a previous run ID")

    # --- status ---
    status_p = sub.add_parser("status", help="Show run status snapshot")
    status_p.add_argument("run_id", nargs="?", default=None, help="Run ID (default: latest)")

    # --- watch ---
    watch_p = sub.add_parser("watch", help="Live dashboard (ANSI, q to quit)")
    watch_p.add_argument("run_id", nargs="?", default=None, help="Run ID (default: latest)")

    # --- log ---
    log_p = sub.add_parser("log", help="Show events for a task")
    log_p.add_argument("task_id", help="Task ID (e.g. task-001)")
    log_p.add_argument("--run", default=None, help="Run ID (default: latest)")
    log_p.add_argument("-f", "--follow", action="store_true", help="Follow mode")
    log_p.add_argument("--raw", action="store_true", help="Show raw JSON lines")
    log_p.add_argument("--kind", default=None, help="Filter by event kind (tool_use, error, denied, text)")

    # --- clean ---
    clean_p = sub.add_parser("clean", help="Clean up worktrees and branches")
    clean_p.add_argument("run_id", nargs="?", default=None, help="Run ID (default: all)")
    clean_p.add_argument("--all", action="store_true", help="Clean all runs")
    clean_p.add_argument("--force", "-f", action="store_true", help="Skip confirmation")

    # --- push ---
    push_p = sub.add_parser("push", help="Push a branch to origin (requires y/N confirmation)")
    push_p.add_argument("branch", help="Branch name to push")

    # --- branches ---
    sub.add_parser("branches", help="List cagent branches")

    # --- plan (v2 stub) ---
    sub.add_parser("plan", help="(Coming in v2) Generate tasks from a goal")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    handlers = {
        "run": _cmd_run,
        "status": _cmd_status,
        "watch": _cmd_watch,
        "log": _cmd_log,
        "clean": _cmd_clean,
        "push": _cmd_push,
        "branches": _cmd_branches,
        "plan": _cmd_plan,
    }
    handlers[args.command](args)


def _fmt_elapsed(seconds: float) -> str:
    """Format elapsed seconds into a human-readable string."""
    secs = int(seconds)
    if secs < 60:
        return f"{secs}s"
    mins, secs = divmod(secs, 60)
    if mins < 60:
        return f"{mins}m{secs}s"
    hours, mins = divmod(mins, 60)
    return f"{hours}h{mins}m{secs}s"


def _preflight_check(check_auth: bool = False) -> None:
    """Verify required tools are available before running.

    If check_auth=True, also verify that `claude -p` can authenticate.
    """
    if not shutil.which("git"):
        print("Error: 'git' not found in PATH. Please install Git.", file=sys.stderr)
        sys.exit(1)
    claude_bin = shutil.which("claude") or shutil.which("claude.cmd")
    if not claude_bin:
        print(
            "Error: 'claude' CLI not found in PATH.\n"
            "  Install Claude Code: https://docs.anthropic.com/en/docs/claude-code\n"
            "  Ensure 'claude' is in your PATH after installation.",
            file=sys.stderr,
        )
        sys.exit(1)

    if check_auth:
        _auth_preflight_check(claude_bin)


def _auth_preflight_check(claude_bin: str) -> None:
    """Run a quick claude -p test to verify authentication works."""
    import os

    print("Checking claude CLI authentication... ", end="", flush=True)
    try:
        result = subprocess.run(
            [claude_bin, "-p", "say hello", "--output-format", "json", "--max-turns", "1"],
            capture_output=True,
            timeout=30,
            env=os.environ.copy(),
        )
        # Decode with error handling for Windows encoding issues
        stdout = result.stdout.decode("utf-8", errors="replace") if result.stdout else ""
        stderr = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
    except subprocess.TimeoutExpired:
        print("TIMEOUT")
        _print_auth_diagnostics()
        sys.exit(1)
    except FileNotFoundError:
        print("FAILED")
        print(f"  Could not execute: {claude_bin}", file=sys.stderr)
        sys.exit(1)

    if result.returncode == 0:
        print("OK")
        return

    # Authentication likely failed — print diagnostics
    print("FAILED")

    # Try to detect the failure reason
    combined = stderr + stdout
    if "apiKeySource" in combined or "403" in combined or "not allowed" in combined.lower():
        print("\n  Authentication failed: claude -p cannot authenticate.", file=sys.stderr)
    elif "not found" in combined.lower():
        print(f"\n  claude CLI not found at: {claude_bin}", file=sys.stderr)
    else:
        print(f"\n  claude -p exited with code {result.returncode}", file=sys.stderr)
        if stderr.strip():
            print(f"  stderr: {stderr.strip()[:200]}", file=sys.stderr)

    _print_auth_diagnostics()
    sys.exit(1)


def _print_auth_diagnostics() -> None:
    """Print environment diagnostics for authentication troubleshooting."""
    import os

    print("\nAuth diagnostics:", file=sys.stderr)
    env_vars = [
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_MODEL",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
    ]
    for var in env_vars:
        val = os.environ.get(var)
        if val is None:
            print(f"  {var}: not set", file=sys.stderr)
        elif var == "ANTHROPIC_API_KEY":
            # Mask the key
            print(f"  {var}: {val[:8]}...{val[-4:]}" if len(val) > 12 else f"  {var}: (set, short)", file=sys.stderr)
        else:
            print(f"  {var}: {val}", file=sys.stderr)

    print("\nPossible fixes:", file=sys.stderr)
    print("  1. Run 'claude auth login' to authenticate via OAuth", file=sys.stderr)
    print("  2. Set a valid API key: export ANTHROPIC_API_KEY=sk-ant-...", file=sys.stderr)
    print("  3. If using a proxy, verify ANTHROPIC_BASE_URL is correct", file=sys.stderr)
    print("  4. Use --api-key flag: cagent run --api-key sk-ant-... tasks.txt", file=sys.stderr)


def _get_repo_root() -> Path:
    """Find the git repo root."""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=True,
    )
    return Path(result.stdout.strip())


def _get_runs_dir(repo_root: Path) -> Path:
    return repo_root / ".cagent" / "runs"


def _find_run_dir(repo_root: Path, run_id: str | None) -> Path:
    """Find a run directory by ID, or the latest one."""
    runs_dir = _get_runs_dir(repo_root)
    if not runs_dir.exists():
        print("No runs found.", file=sys.stderr)
        sys.exit(1)

    if run_id:
        target = runs_dir / run_id
        if not target.exists():
            print(f"Run not found: {run_id}", file=sys.stderr)
            sys.exit(1)
        return target

    # Find latest — accept runs with dashboard.json or tasks.json
    dirs = sorted(runs_dir.iterdir(), reverse=True)
    for d in dirs:
        if d.is_dir() and ((d / "dashboard.json").exists() or (d / "tasks.json").exists()):
            return d
    print("No completed runs found.", file=sys.stderr)
    sys.exit(1)


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
    merge_results: "Callable | None" = None,
    retry_hint: str | None = None,
) -> None:
    """Shared run logic: dispatch → integrate → summary."""
    from cagent.agent import AgentResult
    from cagent.dispatcher import run
    from cagent.integrator import integrate
    from cagent.log import LinePrinter
    from cagent.progress import Dashboard
    from cagent.tasks import dump_state

    run_start = time.time()

    # Set up observability
    dashboard = Dashboard(run_dir)
    printer = LinePrinter(dashboard, quiet=args.quiet)
    dashboard._on_event = printer.push

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

            # Print per-task timing stats
            _print_task_timing(dashboard)

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
            dashboard._on_event = None
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
        dump_state(run_dir, all_tasks)
        done = sum(1 for t in all_tasks if t.status == "done")
        failed = sum(1 for t in all_tasks if t.status == "failed")
        running = sum(1 for t in all_tasks if t.status == "running")
        print(f"  {done} done, {failed} failed, {running} interrupted")
        print(f"  State saved to {run_dir}")
        if not args.keep_worktrees:
            print("  Cleaning up worktrees...")
            _clean_worktrees(repo_root, run_dir, all_tasks, [
                AgentResult(task_id=t.id, status=t.status, commit_sha=t.commit_sha)
                for t in all_tasks
            ])
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


def _cmd_run(args: argparse.Namespace) -> None:
    """Execute the full run workflow: dispatch → integrate → summary."""
    _preflight_check(check_auth=True)

    from cagent.tasks import dump_state, parse_tasks_file
    from cagent.worktree import current_head

    # Inject --api-key into environment if provided
    if args.api_key:
        import os
        os.environ["ANTHROPIC_API_KEY"] = args.api_key

    repo_root = _get_repo_root()

    # Handle --resume
    if args.resume:
        _cmd_resume(args, repo_root)
        return

    # Resolve base SHA
    if args.base:
        result = subprocess.run(
            ["git", "rev-parse", args.base],
            cwd=repo_root, capture_output=True, text=True, check=True,
        )
        base_sha = result.stdout.strip()
    else:
        base_sha = current_head(repo_root)

    # Create run directory
    run_id = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    run_dir = _get_runs_dir(repo_root) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # Parse tasks
    tasks = parse_tasks_file(args.tasks_file, run_id)
    for t in tasks:
        t.log_path = run_dir / "logs" / f"task-{t.id}.log"

    # Dry-run mode: show plan and exit
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

    # Store base SHA for --resume support
    (run_dir / "base_sha").write_text(base_sha, encoding="utf-8")

    print(f"cagent run {run_id}")
    print(f"  base:     {base_sha[:12]}")
    print(f"  tasks:    {len(tasks)}")
    print(f"  jobs:     {args.jobs}")
    print(f"  timeout:  {args.timeout}s")
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

    # Load previous state
    try:
        tasks = load_state(run_dir)
    except FileNotFoundError:
        print(f"No tasks.json found in {run_dir}", file=sys.stderr)
        sys.exit(1)

    run_id = run_dir.name

    # Identify which tasks need to be re-run
    pending_tasks = [t for t in tasks if t.status not in ("done", "noop")]
    done_tasks = [t for t in tasks if t.status in ("done", "noop")]

    if not pending_tasks:
        print(f"All {len(tasks)} tasks already completed. Nothing to resume.")
        return

    print(f"Resuming run {run_id}")
    print(f"  Already done: {len(done_tasks)}")
    print(f"  To run:       {len(pending_tasks)}")
    print()

    # Reset pending tasks and clean stale worktrees
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

    # Resolve base SHA
    base_sha_file = run_dir / "base_sha"
    if base_sha_file.exists():
        base_sha = base_sha_file.read_text(encoding="utf-8").strip()
    else:
        base_sha = current_head(repo_root)

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
    import subprocess

    all_ok = all(r.status in ("done", "noop") for r in results)
    result_map = {r.task_id: r for r in results}

    for task in tasks:
        result = result_map.get(task.id)
        wt_path = repo_root / ".cagent" / "worktrees" / run_dir.name / f"task-{task.id}"
        if wt_path.exists():
            if all_ok or (result and result.status != "failed"):
                # Delete worktree for successful tasks
                try:
                    subprocess.run(
                        ["git", "worktree", "remove", "--force", str(wt_path)],
                        cwd=repo_root, capture_output=True, check=True,
                    )
                except subprocess.CalledProcessError:
                    pass  # Best effort
            # else: keep failed task worktree for debugging


def _cmd_status(args: argparse.Namespace) -> None:
    """Print a one-shot dashboard table."""
    repo_root = _get_repo_root()
    run_dir = _find_run_dir(repo_root, args.run_id)

    dashboard_path = run_dir / "dashboard.json"
    if not dashboard_path.exists():
        print("No dashboard data for this run.", file=sys.stderr)
        sys.exit(1)

    data = json.loads(dashboard_path.read_text(encoding="utf-8"))
    _print_dashboard_table(run_dir.name, data)


def _cmd_watch(args: argparse.Namespace) -> None:
    """Live dashboard with ANSI refresh. Degrades to single status if not TTY."""
    if not is_tty():
        _cmd_status(args)
        print("\n(stdin is not a terminal; use 'cagent watch' in a separate terminal for live updates)")
        return

    enable_ansi()
    repo_root = _get_repo_root()
    run_dir = _find_run_dir(repo_root, args.run_id)
    dashboard_path = run_dir / "dashboard.json"

    print("Press 'q' to quit.\n")

    while True:
        if stdin_has_key():
            key = read_key()
            if key.lower() == "q":
                break

        if dashboard_path.exists():
            data = json.loads(dashboard_path.read_text(encoding="utf-8"))
            # ANSI clear screen + move cursor home
            sys.stdout.write("\033[2J\033[H")
            _print_dashboard_table(run_dir.name, data)
            sys.stdout.flush()

        time.sleep(1)


def _print_dashboard_table(run_id: str, data: dict) -> None:
    """Render a dashboard table from dashboard.json data."""
    # Header
    total = len(data)
    done = sum(1 for v in data.values() if v.get("status") == "done")
    failed = sum(1 for v in data.values() if v.get("status") == "failed")
    running = sum(1 for v in data.values() if v.get("status") == "running")

    status_parts = [f"{done}/{total} done"]
    if running:
        status_parts.append(f"{running} running")
    if failed:
        status_parts.append(f"{failed} failed")
    print(f"RUN: {run_id} │ {' │ '.join(status_parts)}")
    print()

    # Table
    print(f"┌{'─'*12}┬{'─'*10}┬{'─'*10}┬{'─'*6}┬{'─'*32}┐")
    print(f"│ {'task':<10} │ {'status':<8} │ {'elapsed':<8} │ {'tool':<4} │ {'activity':<30} │")
    print(f"├{'─'*12}┼{'─'*10}┼{'─'*10}┼{'─'*6}┼{'─'*32}┤")

    for tid in sorted(data.keys()):
        tp = data[tid]
        status = tp.get("status", "?")
        tool_count = str(tp.get("tool_count", 0))

        # Status with color hints — pad BEFORE adding ANSI codes
        status_padded = f"{status:<8}"
        if status == "done":
            status_display = f"\033[32m{status_padded}\033[0m"
        elif status == "failed":
            status_display = f"\033[31m{status_padded}\033[0m"
        elif status == "running":
            status_display = f"\033[33m{status_padded}\033[0m"
        else:
            status_display = status_padded

        # Elapsed
        elapsed = ""
        if tp.get("started_at"):
            end = tp.get("ended_at") or time.time()
            secs = int(end - tp["started_at"])
            if secs >= 60:
                elapsed = f"{secs // 60}m{secs % 60}s"
            else:
                elapsed = f"{secs}s"

        # Activity — pad before adding ANSI codes
        now = ""
        if tp.get("commit_sha"):
            now = f"commit {tp['commit_sha'][:7]}"
        elif tp.get("last_activity"):
            now = tp["last_activity"][:30]
        elif tp.get("fail_reason"):
            reason_padded = f"{tp['fail_reason'][:30]:<30}"
            now = f"\033[31m{reason_padded}\033[0m"

        print(f"│ {tid:<10} │ {status_display} │ {elapsed:<8} │ {tool_count:<4} │ {now:<30} │")

    print(f"└{'─'*12}┴{'─'*10}┴{'─'*10}┴{'─'*6}┴{'─'*32}┘")


def _cmd_log(args: argparse.Namespace) -> None:
    """Show events for a specific task."""
    repo_root = _get_repo_root()
    run_dir = _find_run_dir(repo_root, args.run)

    task_id = args.task_id.replace("task-", "")
    events_path = run_dir / "events" / f"task-{task_id}.jsonl"
    if not events_path.exists():
        print(f"No events found for task-{task_id}", file=sys.stderr)
        sys.exit(1)

    if args.raw:
        # Raw JSON output
        if args.follow:
            _follow_file(events_path)
        else:
            print(events_path.read_text(encoding="utf-8"), end="")
        return

    # Human-readable output
    if args.follow:
        _follow_events_formatted(events_path, args.kind)
    else:
        _print_events_formatted(events_path, args.kind)


def _print_events_formatted(path: Path, kind_filter: str | None) -> None:
    """Print events in human-readable format."""
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        _print_event_line(line, kind_filter)


def _follow_events_formatted(path: Path, kind_filter: str | None) -> None:
    """Follow events file with human-readable output."""
    with open(path, "r", encoding="utf-8") as f:
        # Print existing content first, then follow
        try:
            while True:
                line = f.readline()
                if line:
                    _print_event_line(line, kind_filter)
                else:
                    time.sleep(0.5)
        except KeyboardInterrupt:
            pass


def _print_event_line(line: str, kind_filter: str | None) -> None:
    """Parse and print a single event line in human-readable format."""
    try:
        ev = json.loads(line)
    except json.JSONDecodeError:
        return

    kind = ev.get("kind", "?")
    if kind_filter and kind != kind_filter:
        return

    ts = ev.get("ts", 0)
    ts_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts else "??:??:??"
    summary = ev.get("summary", "")

    # Color by kind
    color = ""
    reset = "\033[0m"
    if kind == "tool_use":
        color = "\033[36m"  # cyan
    elif kind == "error":
        color = "\033[31m"  # red
    elif kind == "denied":
        color = "\033[33m"  # yellow
    elif kind == "done":
        color = "\033[32m"  # green
    elif kind == "text":
        color = "\033[37m"  # white

    print(f"[{ts_str}] {color}{kind:<12}{reset} {summary}")


def _follow_file(path: Path) -> None:
    """Tail-follow a file (like tail -f)."""
    with open(path, "r", encoding="utf-8") as f:
        # Print existing content first, then follow
        try:
            while True:
                line = f.readline()
                if line:
                    print(line, end="")
                else:
                    time.sleep(0.5)
        except KeyboardInterrupt:
            pass


def _cmd_clean(args: argparse.Namespace) -> None:
    """Clean up worktrees, branches, and run logs."""
    import shutil

    repo_root = _get_repo_root()
    runs_dir = _get_runs_dir(repo_root)

    if not runs_dir.exists():
        print("Nothing to clean.")
        return

    if args.all:
        target_runs = [d for d in runs_dir.iterdir() if d.is_dir()]
    elif args.run_id:
        target_runs = [runs_dir / args.run_id]
        if not target_runs[0].exists():
            print(f"Run not found: {args.run_id}", file=sys.stderr)
            sys.exit(1)
    else:
        # Default to latest run
        dirs = sorted(runs_dir.iterdir(), reverse=True)
        target_runs = [d for d in dirs if d.is_dir()][:1]
        if not target_runs:
            print("No runs found.", file=sys.stderr)
            sys.exit(1)

    if not target_runs:
        print("Nothing to clean.")
        return

    # Show what will be cleaned
    print(f"Will clean {len(target_runs)} run(s):")
    for rd in target_runs:
        wt_base = repo_root / ".cagent" / "worktrees" / rd.name
        wt_count = len(list(wt_base.iterdir())) if wt_base.exists() else 0
        print(f"  {rd.name} ({wt_count} worktrees)")

    # Confirm
    if not args.force:
        try:
            response = input("\nProceed? [y/N] ").strip().lower()
        except EOFError:
            print("Aborted.")
            return
        if response not in ("y", "yes"):
            print("Aborted.")
            return

    for run_dir in target_runs:
        if not run_dir.is_dir():
            continue
        # Remove worktrees
        wt_base = repo_root / ".cagent" / "worktrees" / run_dir.name
        if wt_base.exists():
            for wt in wt_base.iterdir():
                try:
                    subprocess.run(
                        ["git", "worktree", "remove", "--force", str(wt)],
                        cwd=repo_root, capture_output=True,
                    )
                except subprocess.CalledProcessError:
                    pass
            try:
                wt_base.rmdir()
            except OSError:
                pass

        # Delete branches
        run_id = run_dir.name
        try:
            result = subprocess.run(
                ["git", "branch", "--list", f"cagent/{run_id}/*"],
                cwd=repo_root, capture_output=True, text=True,
            )
            for branch in result.stdout.splitlines():
                branch = branch.strip().removeprefix("* ")
                if branch:
                    subprocess.run(
                        ["git", "branch", "-D", branch],
                        cwd=repo_root, capture_output=True,
                    )
        except subprocess.CalledProcessError:
            pass

        # Remove run directory
        shutil.rmtree(run_dir, ignore_errors=True)
        print(f"Cleaned {run_dir.name}")


def _cmd_push(args: argparse.Namespace) -> None:
    """Push a branch to origin with y/N confirmation."""
    import subprocess

    repo_root = _get_repo_root()
    branch = args.branch

    # Verify branch exists
    check = subprocess.run(
        ["git", "rev-parse", "--verify", branch],
        cwd=repo_root, capture_output=True, text=True,
    )
    if check.returncode != 0:
        print(f"Error: branch '{branch}' not found.", file=sys.stderr)
        # Suggest available cagent branches
        result = subprocess.run(
            ["git", "branch", "--list", "cagent/*"],
            cwd=repo_root, capture_output=True, text=True,
        )
        branches = [b.strip().removeprefix("* ") for b in result.stdout.splitlines() if b.strip()]
        if branches:
            print("Available cagent branches:", file=sys.stderr)
            for b in sorted(branches):
                print(f"  {b}", file=sys.stderr)
        sys.exit(1)

    # Show what will be pushed
    result = subprocess.run(
        ["git", "log", "--oneline", f"HEAD..{branch}"],
        cwd=repo_root, capture_output=True, text=True,
    )
    if result.stdout.strip():
        print("Commits to push:")
        print(result.stdout)
    else:
        # Maybe we're on the branch, show recent commits
        result = subprocess.run(
            ["git", "log", "--oneline", "-5", branch],
            cwd=repo_root, capture_output=True, text=True,
        )
        print(f"Recent commits on {branch}:")
        print(result.stdout)

    # Confirm
    try:
        response = input(f"\nPush {branch} to origin? [y/N] ").strip().lower()
    except EOFError:
        print("Aborted.")
        return
    if response not in ("y", "yes"):
        print("Aborted.")
        return

    # Push
    result = subprocess.run(
        ["git", "push", "-u", "origin", branch],
        cwd=repo_root,
    )
    if result.returncode == 0:
        print(f"Pushed {branch} to origin.")
    else:
        print("Push failed.", file=sys.stderr)
        sys.exit(1)


def _cmd_plan(args: argparse.Namespace) -> None:
    """v2 stub."""
    print("cagent plan — coming in v2!")
    print("For now, create a tasks file manually with one task per line.")


def _cmd_branches(args: argparse.Namespace) -> None:
    """List all cagent branches."""
    repo_root = _get_repo_root()
    result = subprocess.run(
        ["git", "branch", "--list", "cagent/*"],
        cwd=repo_root, capture_output=True, text=True,
    )
    branches = [b.strip().removeprefix("* ") for b in result.stdout.splitlines() if b.strip()]
    if not branches:
        print("No cagent branches found.")
        return
    print(f"cagent branches ({len(branches)}):")
    for b in sorted(branches):
        # Show last commit info
        log = subprocess.run(
            ["git", "log", "--oneline", "-1", b],
            cwd=repo_root, capture_output=True, text=True,
        )
        commit = log.stdout.strip()[:60] if log.stdout.strip() else ""
        marker = " *" if b.endswith("/integration") else ""
        print(f"  {b}{marker}  {commit}")
