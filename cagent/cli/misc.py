"""Miscellaneous commands — clean, push, cancel, branches."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from .base import _get_repo_root, _get_runs_dir, _find_run_dir, _terminate_pid


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
        dirs = sorted(runs_dir.iterdir(), reverse=True)
        target_runs = [d for d in dirs if d.is_dir()][:1]
        if not target_runs:
            print("No runs found.", file=sys.stderr)
            sys.exit(1)

    if not target_runs:
        print("Nothing to clean.")
        return

    memory_note = " (including memory)" if args.memory else " (memory preserved)"
    print(f"Will clean {len(target_runs)} run(s){memory_note}:")
    for rd in target_runs:
        wt_base = repo_root / ".cagent" / "worktrees" / rd.name
        wt_count = len(list(wt_base.iterdir())) if wt_base.exists() else 0
        mem_count = len(list((rd / "memory").iterdir())) if (rd / "memory").exists() else 0
        mem_info = f", {mem_count} memory files" if mem_count else ""
        print(f"  {rd.name} ({wt_count} worktrees{mem_info})")

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
        wt_base = repo_root / ".cagent" / "worktrees" / run_dir.name
        if wt_base.exists():
            for wt in list(wt_base.iterdir()):
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

        memory_dir = run_dir / "memory"
        if args.memory or not memory_dir.exists():
            shutil.rmtree(run_dir, ignore_errors=True)
        else:
            for item in list(run_dir.iterdir()):
                if item.name == "memory":
                    continue
                if item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
                else:
                    item.unlink(missing_ok=True)
        print(f"Cleaned {run_dir.name}")


def _cmd_push(args: argparse.Namespace) -> None:
    """Push a branch to origin with y/N confirmation."""
    repo_root = _get_repo_root()
    branch = args.branch

    check = subprocess.run(
        ["git", "rev-parse", "--verify", branch],
        cwd=repo_root, capture_output=True, text=True,
    )
    if check.returncode != 0:
        print(f"Error: branch '{branch}' not found.", file=sys.stderr)
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

    result = subprocess.run(
        ["git", "log", "--oneline", f"HEAD..{branch}"],
        cwd=repo_root, capture_output=True, text=True,
    )
    if result.stdout.strip():
        print("Commits to push:")
        print(result.stdout)
    else:
        result = subprocess.run(
            ["git", "log", "--oneline", "-5", branch],
            cwd=repo_root, capture_output=True, text=True,
        )
        print(f"Recent commits on {branch}:")
        print(result.stdout)

    try:
        response = input(f"\nPush {branch} to origin? [y/N] ").strip().lower()
    except EOFError:
        print("Aborted.")
        return
    if response not in ("y", "yes"):
        print("Aborted.")
        return

    result = subprocess.run(
        ["git", "push", "-u", "origin", branch],
        cwd=repo_root,
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        print(f"Pushed {branch} to origin.")
    else:
        err = (result.stderr or result.stdout or "").strip()
        print(f"Push failed: {err}" if err else "Push failed.", file=sys.stderr)
        sys.exit(1)


def _cmd_cancel(args: argparse.Namespace) -> None:
    """Cancel a running task by sending SIGTERM to its subprocess."""
    repo_root = _get_repo_root()
    run_dir = _find_run_dir(repo_root, args.run)

    task_id = args.task_id.replace("task-", "")
    pid_path = run_dir / "pids" / f"task-{task_id}.pid"

    if not pid_path.exists():
        print(f"No PID file found for task-{task_id}. Task may not be running.", file=sys.stderr)
        sys.exit(1)

    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
    except (ValueError, OSError) as e:
        print(f"Failed to read PID file: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        _terminate_pid(pid)
        print(f"Terminated process {pid} (task-{task_id})")
        pid_path.unlink(missing_ok=True)
    except ProcessLookupError:
        print(f"Process {pid} not found. Task may have already finished.", file=sys.stderr)
        pid_path.unlink(missing_ok=True)


def _cmd_branches(args: argparse.Namespace) -> None:
    """List all cagent branches."""
    repo_root = _get_repo_root()
    result = subprocess.run(
        [
            "git", "for-each-ref",
            "--format=%(refname:short)|%(objectname:short)|%(subject)",
            "refs/heads/cagent/",
        ],
        cwd=repo_root, capture_output=True, text=True,
    )
    if not result.stdout.strip():
        print("No cagent branches found.")
        return
    entries = []
    for line in result.stdout.splitlines():
        parts = line.split("|", 2)
        if len(parts) < 3:
            continue
        name, sha, subject = parts
        entries.append((name, sha, subject))
    print(f"cagent branches ({len(entries)}):")
    for name, sha, subject in sorted(entries):
        commit = f"{sha} {subject}"[:60]
        marker = " *" if name.endswith("/integration") else ""
        print(f"  {name}{marker}  {commit}")
