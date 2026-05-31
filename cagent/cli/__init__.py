"""CLI entry point — argparse subcommands for cagent."""

from __future__ import annotations

import argparse
import sys


def _get_version() -> str:
    """Get cagent version from package metadata or pyproject.toml."""
    try:
        from importlib.metadata import version
        return version("cagent")
    except Exception:  # noqa: BLE001 — non-critical version lookup, graceful degradation
        pass
    # Fallback: read from pyproject.toml
    try:
        from pathlib import Path
        pyproject = Path(__file__).parent.parent.parent / "pyproject.toml"
        if pyproject.exists():
            import re
            match = re.search(r'version\s*=\s*"([^"]+)"', pyproject.read_text(encoding="utf-8"))
            if match:
                return match.group(1)
    except (OSError, ValueError):
        pass
    return "unknown"


def main() -> None:
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

    if sys.version_info < (3, 11):
        print(
            f"cagent requires Python >= 3.11 (found {sys.version}). Please upgrade.",
            file=sys.stderr,
        )
        sys.exit(1)

    parser = argparse.ArgumentParser(
        prog="cagent",
        description="Concurrent agent workflow dispatcher",
    )
    parser.add_argument("--version", action="version", version=f"cagent {_get_version()}")
    sub = parser.add_subparsers(dest="command")

    # --- run ---
    # Config-overridable options default to the UNSET sentinel so that an
    # explicit CLI value (even one equal to the hard default, e.g. --jobs 4)
    # always wins over a value from the config file. apply_config() resolves
    # the sentinel to config-or-default. Non-overridable options keep concrete
    # defaults.
    from cagent.config import UNSET
    run_p = sub.add_parser("run", help="Run tasks from a file concurrently")
    run_p.add_argument("tasks_file", help="Path to tasks file")
    run_p.add_argument("-j", "--jobs", type=int, default=UNSET, help="Concurrency (default: 4)")
    run_p.add_argument("--base", default=None, help="Base branch/SHA (default: HEAD)")
    run_p.add_argument("--squash", action=argparse.BooleanOptionalAction, default=UNSET, help="Squash integration into one commit (--no-squash to disable)")
    run_p.add_argument("--strategy", choices=["cherry-pick", "merge", "rebase"], default=UNSET, help="Integration strategy (default: cherry-pick)")
    run_p.add_argument("--keep-worktrees", action=argparse.BooleanOptionalAction, default=UNSET, help="Keep worktrees after run (--no-keep-worktrees to disable)")
    run_p.add_argument("--worker-model", default=UNSET, help="Model override for workers")
    run_p.add_argument("--integrator-model", default=UNSET, help="Model override for integrator")
    run_p.add_argument("--timeout", type=int, default=UNSET, help="Per-agent timeout in seconds")
    run_p.add_argument("--retries", type=int, default=UNSET, help="Max retries for transient failures (default: 0)")
    run_p.add_argument("--quiet", action=argparse.BooleanOptionalAction, default=UNSET, help="Only print START/DONE/FAIL events (--no-quiet to disable)")
    run_p.add_argument("--api-key", default=None, help="API key for claude -p (WARNING: value visible in process listings; prefer --api-key-file or ANTHROPIC_API_KEY env var)")
    run_p.add_argument("--api-key-file", default=None, help="Read API key from file (safer than --api-key; key is never exposed in process listings)")
    run_p.add_argument("--dry-run", action="store_true", help="Show plan without executing")
    run_p.add_argument("--force", action="store_true", help="Skip run lock check (for concurrent runs)")
    run_p.add_argument("--resume", default=None, help="Resume from a previous run ID")
    run_p.add_argument("--post-integrate-cmd", default=None, help="Command to run after integration (e.g. 'pytest'); failures trigger agent repair, max 2 rounds")
    run_p.add_argument("--max-turns", type=int, default=UNSET, help="Max conversation turns per task (passed to claude -p --max-turns)")
    run_p.add_argument("--max-tokens", type=int, default=UNSET, help="Token budget for entire run (input+output combined); stops dispatching new tasks when exceeded. Note: budget is checked between tasks, so concurrent tasks may overshoot by up to (concurrency-1) tasks worth of tokens.")
    run_p.add_argument("--fail-on-partial", action="store_true", help="Exit non-zero if ANY task fails. By default cagent only exits non-zero on complete failure (no task succeeded) or when integration itself fails.")

    # --- status ---
    status_p = sub.add_parser("status", help="Show run status snapshot")
    status_p.add_argument("run_id", nargs="?", default=None, help="Run ID (default: latest)")

    # --- watch ---
    watch_p = sub.add_parser("watch", help="Live dashboard (ANSI, q to quit)")
    watch_p.add_argument("run_id", nargs="?", default=None, help="Run ID (default: latest)")
    watch_p.add_argument("--web", nargs="?", const=8080, type=int, default=None, help="Start WebSocket server for browser dashboard (default port: 8080)")

    # --- log ---
    log_p = sub.add_parser("log", help="Show events for a task")
    log_p.add_argument("task_id", help="Task ID (e.g. task-001)")
    log_p.add_argument("--run", default=None, help="Run ID (default: latest)")
    log_p.add_argument("-f", "--follow", action="store_true", help="Follow mode")
    log_p.add_argument("--raw", action="store_true", help="Show raw JSON lines")
    log_p.add_argument("--kind", default=None, help="Filter by event kind (tool_use, error, denied, text)")

    # --- clean ---
    clean_p = sub.add_parser("clean", help="Clean up worktrees and branches (memory preserved by default)")
    clean_p.add_argument("run_id", nargs="?", default=None, help="Run ID (default: all)")
    clean_p.add_argument("--all", action="store_true", help="Clean all runs")
    clean_p.add_argument("--force", "-f", action="store_true", help="Skip confirmation")
    clean_p.add_argument("--memory", action="store_true", help="Also delete memory files (preserved by default)")

    # --- push ---
    push_p = sub.add_parser("push", help="Push a branch to origin (requires y/N confirmation)")
    push_p.add_argument("branch", help="Branch name to push")

    # --- branches ---
    sub.add_parser("branches", help="List cagent branches")

    # --- plan ---
    plan_p = sub.add_parser("plan", help="Generate conflict-free tasks from a goal using an architect agent")
    plan_p.add_argument("goal", help="Natural language description of what to build")
    plan_p.add_argument("--ref", help="Reference file to include (e.g. design.md)")
    plan_p.add_argument("--model", help="Model override for the architect agent")

    # --- cancel ---
    cancel_p = sub.add_parser("cancel", help="Cancel a running task")
    cancel_p.add_argument("task_id", help="Task ID to cancel (e.g. 001)")
    cancel_p.add_argument("--run", default=None, help="Run ID (default: latest)")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    from .run import _cmd_run
    from .watch import _cmd_status, _cmd_watch
    from .logcmd import _cmd_log
    from .misc import _cmd_clean, _cmd_push, _cmd_cancel, _cmd_branches
    from .plan import _cmd_plan

    handlers = {
        "run": _cmd_run,
        "status": _cmd_status,
        "watch": _cmd_watch,
        "log": _cmd_log,
        "clean": _cmd_clean,
        "push": _cmd_push,
        "branches": _cmd_branches,
        "plan": _cmd_plan,
        "cancel": _cmd_cancel,
    }
    # Handlers return an int exit code (run) or None (everything else).
    # Exit non-zero only on an explicit nonzero integer code.
    exit_code = handlers[args.command](args)
    if isinstance(exit_code, int) and exit_code != 0:
        sys.exit(exit_code)


# Lazy re-exports for backward compatibility (tests, bin/cagent).
# Using __getattr__ avoids eagerly importing all submodules at CLI startup.
_LAZY_IMPORTS: dict[str, str] = {
    "_fmt_elapsed": ".base",
    "_get_repo_root": ".base",
    "_find_run_dir": ".base",
    "_terminate_pid": ".base",
    "_write_summary": ".run",
    "_print_dashboard_table": ".watch",
    "_cmd_cancel": ".misc",
    "_cmd_clean": ".misc",
}


def __getattr__(name: str):
    if name in _LAZY_IMPORTS:
        import importlib
        mod = importlib.import_module(_LAZY_IMPORTS[name], __package__)
        attr = getattr(mod, name)
        globals()[name] = attr
        return attr
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
