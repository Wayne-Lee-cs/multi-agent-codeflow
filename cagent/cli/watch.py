"""Status and watch commands — dashboard display."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from cagent.compat import enable_ansi, is_tty, stdin_has_key, read_key
from .base import _get_repo_root, _find_run_dir


def _load_budget(run_dir: Path) -> int | None:
    """Load max_tokens budget from budget.json if present."""
    budget_path = run_dir / "budget.json"
    if budget_path.exists():
        try:
            data = json.loads(budget_path.read_text(encoding="utf-8"))
            return data.get("max_tokens")
        except (json.JSONDecodeError, OSError):
            pass
    return None


def _cmd_status(args: argparse.Namespace) -> None:
    """Print a one-shot dashboard table."""
    repo_root = _get_repo_root()
    run_dir = _find_run_dir(repo_root, args.run_id)

    dashboard_path = run_dir / "dashboard.json"
    if not dashboard_path.exists():
        print("No dashboard data for this run.", file=sys.stderr)
        sys.exit(1)

    try:
        data = json.loads(dashboard_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"Error reading dashboard: {e}", file=sys.stderr)
        sys.exit(1)
    max_tokens = _load_budget(run_dir)
    _print_dashboard_table(run_dir.name, data, max_tokens=max_tokens)


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

    import os as _os
    last_mtime: float = 0.0
    last_data: dict | None = None
    needs_redraw: bool = True

    while True:
        if stdin_has_key():
            key = read_key()
            if key.lower() == "q":
                break

        if dashboard_path.exists():
            try:
                mtime = _os.stat(dashboard_path).st_mtime
            except OSError:
                time.sleep(1)
                continue
            if mtime != last_mtime:
                last_mtime = mtime
                try:
                    new_data = json.loads(dashboard_path.read_text(encoding="utf-8"))
                    if new_data != last_data:
                        last_data = new_data
                        needs_redraw = True
                except (json.JSONDecodeError, OSError):
                    time.sleep(1)
                    continue
            if needs_redraw and last_data is not None:
                needs_redraw = False
                sys.stdout.write("\033[2J\033[H")
                _print_dashboard_table(run_dir.name, last_data, max_tokens=_load_budget(run_dir))
                sys.stdout.flush()

        time.sleep(1)


def _print_dashboard_table(run_id: str, data: dict, max_tokens: int | None = None) -> None:
    """Render a dashboard table from dashboard.json data."""
    use_color = sys.stdout.isatty()
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

    has_tokens = any(tp.get("tokens_in", 0) or tp.get("tokens_out", 0) for tp in data.values())

    if has_tokens:
        print(f"┌{'─'*12}┬{'─'*10}┬{'─'*10}┬{'─'*6}┬{'─'*14}┬{'─'*32}┐")
        print(f"│ {'task':<10} │ {'status':<8} │ {'elapsed':<8} │ {'tool':<4} │ {'tokens':<12} │ {'activity':<30} │")
        print(f"├{'─'*12}┼{'─'*10}┼{'─'*10}┼{'─'*6}┼{'─'*14}┼{'─'*32}┤")
    else:
        print(f"┌{'─'*12}┬{'─'*10}┬{'─'*10}┬{'─'*6}┬{'─'*32}┐")
        print(f"│ {'task':<10} │ {'status':<8} │ {'elapsed':<8} │ {'tool':<4} │ {'activity':<30} │")
        print(f"├{'─'*12}┼{'─'*10}┼{'─'*10}┼{'─'*6}┼{'─'*32}┤")

    total_tokens_in = 0
    total_tokens_out = 0

    for tid in sorted(data.keys()):
        tp = data[tid]
        status = tp.get("status", "?")
        tool_count = str(tp.get("tool_count", 0))

        status_padded = f"{status:<8}"
        if not use_color:
            status_display = status_padded
        elif status == "done":
            status_display = f"\033[32m{status_padded}\033[0m"
        elif status == "failed":
            status_display = f"\033[31m{status_padded}\033[0m"
        elif status == "running":
            status_display = f"\033[33m{status_padded}\033[0m"
        else:
            status_display = status_padded

        elapsed = ""
        if tp.get("started_at"):
            end = tp.get("ended_at") or time.time()
            secs = int(end - tp["started_at"])
            if secs >= 60:
                elapsed = f"{secs // 60}m{secs % 60}s"
            else:
                elapsed = f"{secs}s"

        now_raw = ""
        if tp.get("commit_sha"):
            now_raw = f"commit {tp['commit_sha'][:7]}"
        elif tp.get("last_activity"):
            now_raw = tp["last_activity"][:30]
        elif tp.get("fail_reason"):
            now_raw = f"{tp['fail_reason'][:30]}"

        now_padded = f"{now_raw:<30}"
        if use_color and tp.get("fail_reason") and not tp.get("commit_sha"):
            now = f"\033[31m{now_padded}\033[0m"
        else:
            now = now_padded

        if has_tokens:
            t_in = tp.get("tokens_in", 0)
            t_out = tp.get("tokens_out", 0)
            total_tokens_in += t_in
            total_tokens_out += t_out
            tok = f"{t_in:,}→{t_out:,}" if (t_in or t_out) else ""
            print(f"│ {tid:<10} │ {status_display} │ {elapsed:<8} │ {tool_count:<4} │ {tok:<12} │ {now} │")
        else:
            print(f"│ {tid:<10} │ {status_display} │ {elapsed:<8} │ {tool_count:<4} │ {now} │")

    if has_tokens:
        print(f"└{'─'*12}┴{'─'*10}┴{'─'*10}┴{'─'*6}┴{'─'*14}┴{'─'*32}┘")
        if total_tokens_in or total_tokens_out:
            total_combined = total_tokens_in + total_tokens_out
            if max_tokens:
                pct = total_combined * 100 // max_tokens
                budget_str = f" / {max_tokens:,} budget ({pct}%)"
                if pct >= 80 and use_color:
                    budget_str = f"\033[33m{budget_str}\033[0m"
            else:
                budget_str = ""
            print(f"\nTotal tokens: {total_tokens_in:,} in, {total_tokens_out:,} out ({total_combined:,} combined{budget_str})")
    else:
        print(f"└{'─'*12}┴{'─'*10}┴{'─'*10}┴{'─'*6}┴{'─'*32}┘")
