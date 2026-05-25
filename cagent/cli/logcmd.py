"""Log command — show events for a specific task."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from .base import _get_repo_root, _find_run_dir


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
        if args.follow:
            _follow_file(events_path)
        else:
            print(events_path.read_text(encoding="utf-8"), end="")
        return

    if args.follow:
        _follow_events_formatted(events_path, args.kind)
    else:
        _print_events_formatted(events_path, args.kind)


def _print_events_formatted(path: Path, kind_filter: str | None) -> None:
    """Print events in human-readable format."""
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            _print_event_line(line, kind_filter)


def _follow_events_formatted(path: Path, kind_filter: str | None) -> None:
    """Follow events file with human-readable output."""
    _MAX_EMPTY_READS = 60  # 30 seconds at 0.5s intervals
    _MAX_RESETS = 5
    print("(Press Ctrl+C to stop following)\n")
    with open(path, "r", encoding="utf-8") as f:
        empty_count = 0
        reset_count = 0
        try:
            while True:
                line = f.readline()
                if line:
                    empty_count = 0
                    reset_count = 0
                    _print_event_line(line, kind_filter)
                else:
                    empty_count += 1
                    if empty_count >= _MAX_EMPTY_READS:
                        if not path.exists():
                            print(f"\nFile removed: {path}")
                            break
                        reset_count += 1
                        if reset_count >= _MAX_RESETS:
                            break
                        empty_count = 0
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

    color = ""
    reset = "\033[0m"
    if kind == "tool_use":
        color = "\033[36m"
    elif kind == "error":
        color = "\033[31m"
    elif kind == "denied":
        color = "\033[33m"
    elif kind == "done":
        color = "\033[32m"
    elif kind == "text":
        color = "\033[37m"

    print(f"[{ts_str}] {color}{kind:<12}{reset} {summary}")


def _follow_file(path: Path) -> None:
    """Tail-follow a file (like tail -f)."""
    _MAX_EMPTY_READS = 60  # 30 seconds at 0.5s intervals
    _MAX_RESETS = 5
    print("(Press Ctrl+C to stop following)\n")
    with open(path, "r", encoding="utf-8") as f:
        empty_count = 0
        reset_count = 0
        try:
            while True:
                line = f.readline()
                if line:
                    empty_count = 0
                    reset_count = 0
                    print(line, end="")
                else:
                    empty_count += 1
                    if empty_count >= _MAX_EMPTY_READS:
                        if not path.exists():
                            print(f"\nFile removed: {path}")
                            break
                        reset_count += 1
                        if reset_count >= _MAX_RESETS:
                            break
                        empty_count = 0
                    time.sleep(0.5)
        except KeyboardInterrupt:
            pass
